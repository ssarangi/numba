from __future__ import print_function, division, absolute_import
import numpy as np

from llvmlite.llvmpy.core import (Type, Builder, LINKAGE_INTERNAL,
                                  ICMP_EQ, Constant)

from numba import types, cgutils


def _build_ufunc_loop_body(load, store, context, func, builder, arrays, out,
                           offsets, store_offset, signature, pyapi):
    elems = load()

    # Compute
    status, retval = context.call_conv.call_function(builder, func,
                                                     signature.return_type,
                                                     signature.args, elems)

    # Store
    with builder.if_else(status.is_ok, likely=True) as (if_ok, if_error):
        with if_ok:
            store(retval)
        with if_error:
            gil = pyapi.gil_ensure()
            context.call_conv.raise_error(builder, pyapi, status)
            pyapi.gil_release(gil)

    # increment indices
    for off, ary in zip(offsets, arrays):
        builder.store(builder.add(builder.load(off), ary.step), off)

    builder.store(builder.add(builder.load(store_offset), out.step),
                  store_offset)

    return status.code


def _build_ufunc_loop_body_objmode(load, store, context, func, builder,
                                   arrays, out, offsets, store_offset,
                                   signature, env, pyapi):
    elems = load()

    # Compute
    _objargs = [types.pyobject] * len(signature.args)
    # We need to push the error indicator to avoid it messing with
    # the ufunc's execution.  We restore it unless the ufunc raised
    # a new error.
    with pyapi.err_push(keep_new=True):
        status, retval = context.call_conv.call_function(builder, func, types.pyobject,
                                                         _objargs, elems, env=env)
        # Release owned reference to arguments
        for elem in elems:
            pyapi.decref(elem)
    # NOTE: if an error occurred, it will be caught by the Numpy machinery

    # Store
    store(retval)

    # increment indices
    for off, ary in zip(offsets, arrays):
        builder.store(builder.add(builder.load(off), ary.step), off)

    builder.store(builder.add(builder.load(store_offset), out.step),
                  store_offset)

    return status.code


def build_slow_loop_body(context, func, builder, arrays, out, offsets,
                         store_offset, signature, pyapi):
    def load():
        elems = [ary.load_direct(builder.load(off))
                 for off, ary in zip(offsets, arrays)]
        return elems

    def store(retval):
        out.store_direct(retval, builder.load(store_offset))

    return _build_ufunc_loop_body(load, store, context, func, builder, arrays,
                                  out, offsets, store_offset, signature, pyapi)


def build_obj_loop_body(context, func, builder, arrays, out, offsets,
                        store_offset, signature, pyapi, envptr, env):
    env_body = context.get_env_body(builder, envptr)
    env_manager = pyapi.get_env_manager(env, env_body, envptr)

    def load():
        # Load
        elems = [ary.load_direct(builder.load(off))
                 for off, ary in zip(offsets, arrays)]
        # Box
        elems = [pyapi.from_native_value(t, v, env_manager)
                 for v, t in zip(elems, signature.args)]
        return elems

    def store(retval):
        is_ok = cgutils.is_not_null(builder, retval)
        # If an error is raised by the object mode ufunc, it will
        # simply get caught by the Numpy ufunc machinery.
        with builder.if_then(is_ok, likely=True):
            # Unbox
            native = pyapi.to_native_value(signature.return_type, retval)
            assert native.cleanup is None
            # Store
            out.store_direct(native.value, builder.load(store_offset))
            # Release owned reference
            pyapi.decref(retval)

    return _build_ufunc_loop_body_objmode(load, store, context, func, builder,
                                          arrays, out, offsets, store_offset,
                                          signature, envptr, pyapi)


def build_fast_loop_body(context, func, builder, arrays, out, offsets,
                         store_offset, signature, ind, pyapi):
    def load():
        elems = [ary.load_aligned(ind)
                 for ary in arrays]
        return elems

    def store(retval):
        out.store_aligned(retval, ind)

    return _build_ufunc_loop_body(load, store, context, func, builder, arrays,
                                  out, offsets, store_offset, signature, pyapi)


def build_ufunc_wrapper(library, context, func, signature, objmode, envptr, env):
    """
    Wrap the scalar function with a loop that iterates over the arguments
    """
    byte_t = Type.int(8)
    byte_ptr_t = Type.pointer(byte_t)
    byte_ptr_ptr_t = Type.pointer(byte_ptr_t)
    intp_t = context.get_value_type(types.intp)
    intp_ptr_t = Type.pointer(intp_t)

    fnty = Type.function(Type.void(), [byte_ptr_ptr_t, intp_ptr_t,
                                       intp_ptr_t, byte_ptr_t])

    wrapper_module = library.create_ir_module('')
    if objmode:
        func_type = context.call_conv.get_function_type(
            types.pyobject, [types.pyobject] * len(signature.args))
    else:
        func_type = context.call_conv.get_function_type(
            signature.return_type, signature.args)
    oldfunc = func
    func = wrapper_module.add_function(func_type,
                                       name=func.name)
    func.attributes.add("alwaysinline")

    wrapper = wrapper_module.add_function(fnty, "__ufunc__." + func.name)
    arg_args, arg_dims, arg_steps, arg_data = wrapper.args
    arg_args.name = "args"
    arg_dims.name = "dims"
    arg_steps.name = "steps"
    arg_data.name = "data"

    builder = Builder.new(wrapper.append_basic_block("entry"))

    loopcount = builder.load(arg_dims, name="loopcount")

    # Prepare inputs
    arrays = []
    for i, typ in enumerate(signature.args):
        arrays.append(UArrayArg(context, builder, arg_args, arg_steps, i, typ))

    # Prepare output
    out = UArrayArg(context, builder, arg_args, arg_steps, len(arrays),
                    signature.return_type)

    # Setup indices
    offsets = []
    zero = context.get_constant(types.intp, 0)
    for _ in arrays:
        p = cgutils.alloca_once(builder, intp_t)
        offsets.append(p)
        builder.store(zero, p)

    store_offset = cgutils.alloca_once(builder, intp_t)
    builder.store(zero, store_offset)

    unit_strided = cgutils.true_bit
    for ary in arrays:
        unit_strided = builder.and_(unit_strided, ary.is_unit_strided)

    pyapi = context.get_python_api(builder)
    if objmode:
        # General loop
        gil = pyapi.gil_ensure()
        with cgutils.for_range(builder, loopcount, intp=intp_t):
            slowloop = build_obj_loop_body(context, func, builder,
                                           arrays, out, offsets,
                                           store_offset, signature,
                                           pyapi, envptr, env)
        pyapi.gil_release(gil)
        builder.ret_void()

    else:
        with builder.if_else(unit_strided) as (is_unit_strided, is_strided):
            with is_unit_strided:
                with cgutils.for_range(builder, loopcount, intp=intp_t) as loop:
                    fastloop = build_fast_loop_body(context, func, builder,
                                                    arrays, out, offsets,
                                                    store_offset, signature,
                                                    loop.index, pyapi)

            with is_strided:
                # General loop
                with cgutils.for_range(builder, loopcount, intp=intp_t):
                    slowloop = build_slow_loop_body(context, func, builder,
                                                    arrays, out, offsets,
                                                    store_offset, signature,
                                                    pyapi)

        builder.ret_void()
    del builder

    # Run optimizer
    library.add_ir_module(wrapper_module)
    wrapper = library.get_function(wrapper.name)

    return wrapper


class UArrayArg(object):
    def __init__(self, context, builder, args, steps, i, fe_type):
        self.context = context
        self.builder = builder
        self.fe_type = fe_type
        offset = self.context.get_constant(types.intp, i)
        offseted_args = self.builder.load(builder.gep(args, [offset]))
        data_type = context.get_data_type(fe_type)
        self.dataptr = self.builder.bitcast(offseted_args,
                                            data_type.as_pointer())
        sizeof = self.context.get_abi_sizeof(data_type)
        self.abisize = self.context.get_constant(types.intp, sizeof)
        offseted_step = self.builder.gep(steps, [offset])
        self.step = self.builder.load(offseted_step)
        self.is_unit_strided = builder.icmp(ICMP_EQ, self.abisize, self.step)
        self.builder = builder

    def load_direct(self, byteoffset):
        """
        Generic load from the given *byteoffset*.  load_aligned() is
        preferred if possible.
        """
        ptr = cgutils.pointer_add(self.builder, self.dataptr, byteoffset)
        return self.context.unpack_value(self.builder, self.fe_type, ptr)

    def load_aligned(self, ind):
        # Using gep() instead of explicit pointer addition helps LLVM
        # vectorize the loop.
        ptr = self.builder.gep(self.dataptr, [ind])
        return self.context.unpack_value(self.builder, self.fe_type, ptr)

    def store_direct(self, value, byteoffset):
        ptr = cgutils.pointer_add(self.builder, self.dataptr, byteoffset)
        self.context.pack_value(self.builder, self.fe_type, value, ptr)

    def store_aligned(self, value, ind):
        ptr = self.builder.gep(self.dataptr, [ind])
        self.context.pack_value(self.builder, self.fe_type, value, ptr)


class _GufuncWrapper(object):
    def __init__(self, library, context, func, signature, sin, sout, fndesc,
                 env):
        self.library = library
        self.context = context
        self.call_conv = context.call_conv
        self.func = func
        self.signature = signature
        self.sin = sin
        self.sout = sout
        self.fndesc = fndesc
        self.is_objectmode = self.signature.return_type == types.pyobject
        self.env = env

    def build(self):
        byte_t = Type.int(8)
        byte_ptr_t = Type.pointer(byte_t)
        byte_ptr_ptr_t = Type.pointer(byte_ptr_t)
        intp_t = self.context.get_value_type(types.intp)
        intp_ptr_t = Type.pointer(intp_t)

        fnty = Type.function(Type.void(), [byte_ptr_ptr_t, intp_ptr_t,
                                           intp_ptr_t, byte_ptr_t])

        wrapper_module = self.library.create_ir_module('')
        func_type = self.call_conv.get_function_type(self.fndesc.restype,
                                                     self.fndesc.argtypes)
        func = wrapper_module.add_function(func_type, name=self.func.name)
        func.attributes.add("alwaysinline")
        wrapper = wrapper_module.add_function(fnty,
                                              "__gufunc__." + self.func.name)
        arg_args, arg_dims, arg_steps, arg_data = wrapper.args
        arg_args.name = "args"
        arg_dims.name = "dims"
        arg_steps.name = "steps"
        arg_data.name = "data"

        builder = Builder.new(wrapper.append_basic_block("entry"))
        loopcount = builder.load(arg_dims, name="loopcount")
        pyapi = self.context.get_python_api(builder)

        # Unpack shapes
        unique_syms = set()
        for grp in (self.sin, self.sout):
            for syms in grp:
                unique_syms |= set(syms)

        sym_map = {}
        for syms in self.sin:
            for s in syms:
                if s not in sym_map:
                    sym_map[s] = len(sym_map)

        sym_dim = {}
        for s, i in sym_map.items():
            sym_dim[s] = builder.load(builder.gep(arg_dims,
                                                  [self.context.get_constant(
                                                      types.intp,
                                                      i + 1)]))

        # Prepare inputs
        arrays = []
        step_offset = len(self.sin) + len(self.sout)
        for i, (typ, sym) in enumerate(zip(self.signature.args,
                                           self.sin + self.sout)):
            ary = GUArrayArg(self.context, builder, arg_args, arg_dims,
                             arg_steps, i, step_offset, typ, sym, sym_dim)
            if not ary.as_scalar:
                step_offset += ary.ndim
            arrays.append(ary)

        bbreturn = builder.append_basic_block('.return')

        # Prologue
        self.gen_prologue(builder, pyapi)

        # Loop
        with cgutils.for_range(builder, loopcount, intp=intp_t) as loop:
            args = [a.get_array_at_offset(loop.index) for a in arrays]
            innercall, error = self.gen_loop_body(builder, pyapi, func, args)
            # If error, escape
            cgutils.cbranch_or_continue(builder, error, bbreturn)

        builder.branch(bbreturn)
        builder.position_at_end(bbreturn)

        # Epilogue
        self.gen_epilogue(builder, pyapi)

        builder.ret_void()

        self.library.add_ir_module(wrapper_module)
        wrapper = self.library.get_function(wrapper.name)

        # Set core function to internal so that it is not generated
        self.func.linkage = LINKAGE_INTERNAL

        return wrapper, self.env

    def gen_loop_body(self, builder, pyapi, func, args):
        status, retval = self.call_conv.call_function(builder, func,
                                                      self.signature.return_type,
                                                      self.signature.args, args)

        with builder.if_then(status.is_error, likely=False):
            gil = pyapi.gil_ensure()
            self.context.call_conv.raise_error(builder, pyapi, status)
            pyapi.gil_release(gil)

        return status.code, status.is_error

    def gen_prologue(self, builder, pyapi):
        pass        # Do nothing

    def gen_epilogue(self, builder, pyapi):
        pass        # Do nothing


class _GufuncObjectWrapper(_GufuncWrapper):
    def gen_loop_body(self, builder, pyapi, func, args):
        innercall, error = _prepare_call_to_object_mode(self.context,
                                                        builder, pyapi, func,
                                                        self.signature,
                                                        args, env=self.envptr)
        return innercall, error

    def gen_prologue(self, builder, pyapi):
        #  Get an environment object for the function
        ll_intp = self.context.get_value_type(types.intp)
        ll_pyobj = self.context.get_value_type(types.pyobject)
        self.envptr = Constant.int(ll_intp, id(self.env)).inttoptr(ll_pyobj)

        # Acquire the GIL
        self.gil = pyapi.gil_ensure()

    def gen_epilogue(self, builder, pyapi):
        # Release GIL
        pyapi.gil_release(self.gil)


def build_gufunc_wrapper(library, context, func, signature, sin, sout, fndesc,
                         env):
    wrapcls = (_GufuncObjectWrapper
               if signature.return_type == types.pyobject
               else _GufuncWrapper)
    return wrapcls(library, context, func, signature, sin, sout, fndesc,
                   env).build()


def _prepare_call_to_object_mode(context, builder, pyapi, func,
                                 signature, args, env):
    mod = builder.module

    bb_core_return = builder.append_basic_block('ufunc.core.return')

    # Call to
    # PyObject* ndarray_new(int nd,
    #       npy_intp *dims,   /* shape */
    #       npy_intp *strides,
    #       void* data,
    #       int type_num,
    #       int itemsize)

    ll_int = context.get_value_type(types.int32)
    ll_intp = context.get_value_type(types.intp)
    ll_intp_ptr = Type.pointer(ll_intp)
    ll_voidptr = context.get_value_type(types.voidptr)
    ll_pyobj = context.get_value_type(types.pyobject)
    fnty = Type.function(ll_pyobj, [ll_int, ll_intp_ptr,
                                    ll_intp_ptr, ll_voidptr,
                                    ll_int, ll_int])

    fn_array_new = mod.get_or_insert_function(fnty, name="numba_ndarray_new")

    # Convert each llarray into pyobject
    error_pointer = cgutils.alloca_once(builder, Type.int(1), name='error')
    builder.store(cgutils.true_bit, error_pointer)
    ndarray_pointers = []
    ndarray_objects = []
    for i, (arr, arrtype) in enumerate(zip(args, signature.args)):
        ptr = cgutils.alloca_once(builder, ll_pyobj)
        ndarray_pointers.append(ptr)

        builder.store(Constant.null(ll_pyobj), ptr)   # initialize to NULL

        arycls = context.make_array(arrtype)
        array = arycls(context, builder, value=arr)

        zero = Constant.int(ll_int, 0)

        # Extract members of the llarray
        nd = Constant.int(ll_int, arrtype.ndim)
        dims = builder.gep(array._get_ptr_by_name('shape'), [zero, zero])
        strides = builder.gep(array._get_ptr_by_name('strides'), [zero, zero])
        data = builder.bitcast(array.data, ll_voidptr)
        dtype = np.dtype(str(arrtype.dtype))

        # Prepare other info for reconstruction of the PyArray
        type_num = Constant.int(ll_int, dtype.num)
        itemsize = Constant.int(ll_int, dtype.itemsize)

        # Call helper to reconstruct PyArray objects
        obj = builder.call(fn_array_new, [nd, dims, strides, data,
                                          type_num, itemsize])
        builder.store(obj, ptr)
        ndarray_objects.append(obj)

        obj_is_null = cgutils.is_null(builder, obj)
        builder.store(obj_is_null, error_pointer)
        cgutils.cbranch_or_continue(builder, obj_is_null, bb_core_return)

    # Call ufunc core function
    object_sig = [types.pyobject] * len(ndarray_objects)

    status, retval = context.call_conv.call_function(
        builder, func, types.pyobject, object_sig,
        ndarray_objects, env=env)
    builder.store(status.is_error, error_pointer)

    # Release returned object
    pyapi.decref(retval)

    builder.branch(bb_core_return)
    # At return block
    builder.position_at_end(bb_core_return)

    # Release argument object
    for ndary_ptr in ndarray_pointers:
        pyapi.decref(builder.load(ndary_ptr))

    innercall = status.code
    return innercall, builder.load(error_pointer)


class GUArrayArg(object):
    def __init__(self, context, builder, args, dims, steps, i, step_offset,
                 typ, syms, sym_dim):

        self.context = context
        self.builder = builder

        if isinstance(typ, types.Array):
            self.dtype = typ.dtype
        else:
            self.dtype = typ

        self.syms = syms
        self.as_scalar = not syms

        offset = context.get_constant(types.intp, i)

        core_step_ptr = builder.gep(steps, [offset], name="core.step.ptr")
        self.core_step = builder.load(core_step_ptr)

        if self.as_scalar:
            self.ndim = 1
        else:
            self.ndim = len(syms)
            self.shape = []
            self.strides = []

            for j in range(self.ndim):
                stepptr = builder.gep(steps,
                                      [context.get_constant(types.intp,
                                                            step_offset + j)],
                                      name="step.ptr")

                step = builder.load(stepptr)
                self.strides.append(step)

            for s in syms:
                self.shape.append(sym_dim[s])

        data = builder.load(builder.gep(args, [offset], name="data.ptr"),
                            name="data")

        self.data = data

    def get_array_at_offset(self, ind):
        context = self.context
        builder = self.builder

        arytyp = types.Array(dtype=self.dtype, ndim=self.ndim, layout="A")
        arycls = context.make_array(arytyp)

        array = arycls(context, builder)
        offseted_data = cgutils.pointer_add(self.builder,
                                            self.data,
                                            self.builder.mul(self.core_step,
                                                             ind))
        if not self.as_scalar:
            shape = cgutils.pack_array(builder, self.shape)
            strides = cgutils.pack_array(builder, self.strides)
        else:
            one = context.get_constant(types.intp, 1)
            zero = context.get_constant(types.intp, 0)
            shape = cgutils.pack_array(builder, [one])
            strides = cgutils.pack_array(builder, [zero])

        itemsize = context.get_abi_sizeof(context.get_data_type(self.dtype))
        context.populate_array(array,
                               data=builder.bitcast(offseted_data,
                                                    array.data.type),
                               shape=shape,
                               strides=strides,
                               itemsize=context.get_constant(types.intp,
                                                             itemsize),
                               meminfo=None)

        return array._getvalue()

