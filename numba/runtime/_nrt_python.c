#include "_pymodule.h"
#include "nrt.h"

#define NPY_NO_DEPRECATED_API NPY_1_7_API_VERSION
#include <numpy/ndarrayobject.h>
#include <numpy/arrayscalars.h>

#include "_arraystruct.h"

/* For Numpy 1.6 */
#ifndef NPY_ARRAY_BEHAVED
    #define NPY_ARRAY_BEHAVED NPY_BEHAVED
#endif


static
PyObject*
memsys_shutdown(PyObject *self, PyObject *args) {
    NRT_MemSys_shutdown();
    Py_RETURN_NONE;
}

static PyObject *
memsys_use_cpython_allocator(PyObject *self, PyObject *args) {
    NRT_MemSys_set_allocator(PyMem_RawMalloc,
                             PyMem_RawRealloc,
                             PyMem_RawFree);
    Py_RETURN_NONE;
}

static
PyObject*
memsys_set_atomic_inc_dec(PyObject *self, PyObject *args) {
    PyObject *addr_inc_obj, *addr_dec_obj;
    void *addr_inc, *addr_dec;
    if (!PyArg_ParseTuple(args, "OO", &addr_inc_obj, &addr_dec_obj)) {
        return NULL;
    }
    addr_inc = PyLong_AsVoidPtr(addr_inc_obj);
    if(PyErr_Occurred()) return NULL;
    addr_dec = PyLong_AsVoidPtr(addr_dec_obj);
    if(PyErr_Occurred()) return NULL;
    NRT_MemSys_set_atomic_inc_dec(addr_inc, addr_dec);
    Py_RETURN_NONE;
}

static
PyObject*
memsys_set_atomic_cas(PyObject *self, PyObject *args) {
    PyObject *addr_cas_obj;
    void *addr_cas;
    if (!PyArg_ParseTuple(args, "O", &addr_cas_obj)) {
        return NULL;
    }
    addr_cas = PyLong_AsVoidPtr(addr_cas_obj);
    if(PyErr_Occurred()) return NULL;
    NRT_MemSys_set_atomic_cas(addr_cas);
    Py_RETURN_NONE;
}

static
PyObject*
memsys_get_stats_alloc(PyObject *self, PyObject *args) {
    return PyLong_FromSize_t(NRT_MemSys_get_stats_alloc());
}

static
PyObject*
memsys_get_stats_free(PyObject *self, PyObject *args) {
    return PyLong_FromSize_t(NRT_MemSys_get_stats_free());
}

static
PyObject*
memsys_get_stats_mi_alloc(PyObject *self, PyObject *args) {
    return PyLong_FromSize_t(NRT_MemSys_get_stats_mi_alloc());
}

static
PyObject*
memsys_get_stats_mi_free(PyObject *self, PyObject *args) {
    return PyLong_FromSize_t(NRT_MemSys_get_stats_mi_free());
}

static
void pyobject_dtor(void *ptr, void* info) {
    PyGILState_STATE gstate;
    PyObject *ownerobj = info;

    gstate = PyGILState_Ensure();   /* ensure the GIL */
    Py_DECREF(ownerobj);            /* release the python object */
    PyGILState_Release(gstate);     /* release the GIL */
}


static
NRT_MemInfo *meminfo_new_from_pyobject(void *data, PyObject *ownerobj) {
    size_t dummy_size = 0;
    Py_INCREF(ownerobj);
    return NRT_MemInfo_new(data, dummy_size, pyobject_dtor, ownerobj);
}


/*
 * Create a new MemInfo with a owner PyObject
 */
static PyObject *
meminfo_new(PyObject *self, PyObject *args) {
    PyObject *addr_data_obj;
    void *addr_data;
    PyObject *ownerobj;
    NRT_MemInfo *mi;
    if (!PyArg_ParseTuple(args, "OO", &addr_data_obj, &ownerobj)) {
        return NULL;
    }
    addr_data = PyLong_AsVoidPtr(addr_data_obj);
    if(PyErr_Occurred()) return NULL;
    mi = meminfo_new_from_pyobject(addr_data, ownerobj);
    return PyLong_FromVoidPtr(mi);
}

/*
 * Create a new MemInfo with a new NRT allocation
 */
static PyObject *
meminfo_alloc(PyObject *self, PyObject *args) {
    NRT_MemInfo *mi;
    Py_ssize_t size;
    if (!PyArg_ParseTuple(args, "n", &size)) {
        return NULL;
    }
    mi = NRT_MemInfo_alloc(size);
    return PyLong_FromVoidPtr(mi);
}

/*
 * Like meminfo_alloc but set memory to zero after allocation and before
 * deallocation.
 */
static PyObject *
meminfo_alloc_safe(PyObject *self, PyObject *args) {
    NRT_MemInfo *mi;
    Py_ssize_t size;
    if (!PyArg_ParseTuple(args, "n", &size)) {
        return NULL;
    }
    mi = NRT_MemInfo_alloc_safe(size);
    return PyLong_FromVoidPtr(mi);
}

typedef struct {
    PyObject_HEAD
    NRT_MemInfo *meminfo;
} MemInfoObject;

static
int MemInfo_init(MemInfoObject *self, PyObject *args, PyObject *kwds) {
    static char *keywords[] = {"ptr", NULL};
    PyObject *raw_ptr_obj;
    void *raw_ptr;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "O", keywords, &raw_ptr_obj)) {
        return -1;
    }
    raw_ptr = PyLong_AsVoidPtr(raw_ptr_obj);
    if(PyErr_Occurred()) return -1;
    self->meminfo = (NRT_MemInfo *)raw_ptr;
    assert (NRT_MemInfo_refcount(self->meminfo) > 0 && "0 refcount");
    return 0;
}

int MemInfo_getbuffer(PyObject *exporter, Py_buffer *view, int flags) {
    Py_ssize_t len;
    void *buf;
    int readonly = 0;

    MemInfoObject *miobj = (MemInfoObject*)exporter;
    NRT_MemInfo *mi = miobj->meminfo;

    buf = NRT_MemInfo_data(mi);
    len = NRT_MemInfo_size(mi);
    return PyBuffer_FillInfo(view, exporter, buf, len, readonly, flags);
}

Py_ssize_t MemInfo_rdwrbufferproc(PyObject *self, Py_ssize_t segment,
                                  void **ptrptr)
{
    MemInfoObject *mio = (MemInfoObject *)self;
    NRT_MemInfo *mi = mio->meminfo;
    if (segment != 0) {
        PyErr_SetString(PyExc_TypeError, "MemInfo only has 1 segment");
        return -1;
    }
    *ptrptr = NRT_MemInfo_data(mi);
    return NRT_MemInfo_size(mi);
}

Py_ssize_t MemInfo_segcountproc(PyObject *self, Py_ssize_t *lenp) {
    MemInfoObject *mio = (MemInfoObject *)self;
    NRT_MemInfo *mi = mio->meminfo;
    if (lenp) {
        *lenp = NRT_MemInfo_size(mi);
    }
    return 1;
}

#if (PY_MAJOR_VERSION < 3)
static PyBufferProcs MemInfo_bufferProcs = {MemInfo_rdwrbufferproc,
                                            MemInfo_rdwrbufferproc,
                                            MemInfo_segcountproc,
                                            NULL};
#else
static PyBufferProcs MemInfo_bufferProcs = {MemInfo_getbuffer, NULL};
#endif

static
PyObject*
MemInfo_acquire(MemInfoObject *self) {
    NRT_MemInfo_acquire(self->meminfo);
    Py_RETURN_NONE;
}

static
PyObject*
MemInfo_release(MemInfoObject *self) {
    NRT_MemInfo_release(self->meminfo);
    Py_RETURN_NONE;
}

static
PyObject*
MemInfo_get_data(MemInfoObject *self, void *closure) {
    return PyLong_FromVoidPtr(NRT_MemInfo_data(self->meminfo));
}

static
PyObject*
MemInfo_get_refcount(MemInfoObject *self, void *closure) {
    size_t refct = NRT_MemInfo_refcount(self->meminfo);
    if ( refct == (size_t)-1 ) {
        PyErr_SetString(PyExc_ValueError, "invalid MemInfo");
        return NULL;
    }
    return PyLong_FromSize_t(refct);
}

static void
MemInfo_dealloc(MemInfoObject *self)
{
    NRT_MemInfo_release(self->meminfo);
    Py_TYPE(self)->tp_free((PyObject*)self);
}

static PyMethodDef MemInfo_methods[] = {
    {"acquire", (PyCFunction)MemInfo_acquire, METH_NOARGS,
     "Increment the reference count"
    },
    {"release", (PyCFunction)MemInfo_release, METH_NOARGS,
     "Decrement the reference count"
    },
    {NULL}  /* Sentinel */
};


static PyGetSetDef MemInfo_getsets[] = {
    {"data",
     (getter)MemInfo_get_data, NULL,
     "Get the data pointer as an integer",
     NULL},
    {"refcount",
     (getter)MemInfo_get_refcount, NULL,
     "Get the refcount",
     NULL},
    {NULL}  /* Sentinel */
};


static PyTypeObject MemInfoType = {
#if (PY_MAJOR_VERSION < 3)
    PyObject_HEAD_INIT(NULL)
    0,                                        /* ob_size*/
#else
    PyVarObject_HEAD_INIT(NULL, 0)
#endif
    "_nrt_python._MemInfo",                   /* tp_name*/
    sizeof(MemInfoObject),                    /* tp_basicsize*/
    0,                                        /* tp_itemsize*/
    (destructor)MemInfo_dealloc,              /* tp_dealloc*/
    0,                                        /* tp_print*/
    0,                                        /* tp_getattr*/
    0,                                        /* tp_setattr*/
    0,                                        /* tp_compare*/
    0,                                        /* tp_repr*/
    0,                                        /* tp_as_number*/
    0,                                        /* tp_as_sequence*/
    0,                                        /* tp_as_mapping*/
    0,                                        /* tp_hash */
    0,                                        /* tp_call*/
    0,                                        /* tp_str*/
    0,                                        /* tp_getattro*/
    0,                                        /* tp_setattro*/
    &MemInfo_bufferProcs,                      /* tp_as_buffer*/
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,  /* tp_flags*/
    0,                                        /* tp_doc */
    0,                                        /* tp_traverse */
    0,                                        /* tp_clear */
    0,                                        /* tp_richcompare */
    0,                                        /* tp_weaklistoffset */
    0,                                        /* tp_iter */
    0,                                        /* tp_iternext */
    MemInfo_methods,                          /* tp_methods */
    0,                                        /* tp_members */
    MemInfo_getsets,                          /* tp_getset */
    0,                                        /* tp_base */
    0,                                        /* tp_dict */
    0,                                        /* tp_descr_get */
    0,                                        /* tp_descr_set */
    0,                                        /* tp_dictoffset */
    (initproc)MemInfo_init,                   /* tp_init */
    0,                                        /* tp_alloc */
    0,                                        /* tp_new */
};


/****** Array adaptor code ******/


static
int NRT_adapt_ndarray_from_python(PyObject *obj, arystruct_t* arystruct) {
    PyArrayObject *ndary;
    int i, ndim;
    npy_intp *p;
    void *data;

    if (!PyArray_Check(obj)) {
        return -1;
    }

    ndary = (PyArrayObject*)obj;
    ndim = PyArray_NDIM(ndary);
    data = PyArray_DATA(ndary);

    arystruct->meminfo = meminfo_new_from_pyobject((void*)data, obj);
    arystruct->data = data;
    arystruct->nitems = PyArray_SIZE(ndary);
    arystruct->itemsize = PyArray_ITEMSIZE(ndary);
    arystruct->parent = obj;
    p = arystruct->shape_and_strides;
    for (i = 0; i < ndim; i++, p++) {
        *p = PyArray_DIM(ndary, i);
    }
    for (i = 0; i < ndim; i++, p++) {
        *p = PyArray_STRIDE(ndary, i);
    }

    NRT_Debug(nrt_debug_print("NRT_adapt_ndarray_from_python %p\n",
                              arystruct->meminfo));
    return 0;
}

static
PyObject* try_to_return_parent(arystruct_t *arystruct, int ndim,
                               PyArray_Descr *descr)
{
    int i;
    PyArrayObject *array = (PyArrayObject *)arystruct->parent;

    if (!PyArray_Check(arystruct->parent))
        /* Parent is a generic buffer-providing object */
        goto RETURN_ARRAY_COPY;

    if (PyArray_DATA(array) != arystruct->data)
        goto RETURN_ARRAY_COPY;

    if (PyArray_NDIM(array) != ndim)
        goto RETURN_ARRAY_COPY;

    if (PyObject_RichCompareBool((PyObject *) PyArray_DESCR(array),
                                 (PyObject *) descr, Py_EQ) <= 0)
        goto RETURN_ARRAY_COPY;

    for(i = 0; i < ndim; ++i) {
        if (PyArray_DIMS(array)[i] != arystruct->shape_and_strides[i])
            goto RETURN_ARRAY_COPY;
        if (PyArray_STRIDES(array)[i] != arystruct->shape_and_strides[ndim + i])
            goto RETURN_ARRAY_COPY;
    }

    /* Yes, it is the same array
       Return new reference */
    Py_INCREF((PyObject *)array);
    return (PyObject *)array;

RETURN_ARRAY_COPY:
    return NULL;
}

static PyObject *
NRT_adapt_ndarray_to_python(arystruct_t* arystruct, int ndim,
                            int writeable, PyArray_Descr *descr)
{
    PyArrayObject *array;
    MemInfoObject *miobj = NULL;
    PyObject *args;
    npy_intp *shape, *strides;
    int flags = 0;

    if (!PyArray_DescrCheck(descr)) {
        PyErr_Format(PyExc_TypeError,
                     "expected dtype object, got '%.200s'",
                     Py_TYPE(descr)->tp_name);
        return NULL;
    }

    if (arystruct->parent) {
        PyObject *obj = try_to_return_parent(arystruct, ndim, descr);
        if (obj) {
            /* Release NRT reference to the numpy array */
            NRT_MemInfo_release(arystruct->meminfo);
            return obj;
        }
    }

    if (arystruct->meminfo) {
        /* wrap into MemInfoObject */
        miobj = PyObject_New(MemInfoObject, &MemInfoType);
        args = PyTuple_New(1);
        /* SETITEM steals reference */
        PyTuple_SET_ITEM(args, 0, PyLong_FromVoidPtr(arystruct->meminfo));
        /*  Note: MemInfo_init() does not incref.  This function steals the
         *        NRT reference.
         */
        if (MemInfo_init(miobj, args, NULL)) {
            return NULL;
        }
        Py_DECREF(args);
    }

    shape = arystruct->shape_and_strides;
    strides = shape + ndim;
    Py_INCREF((PyObject *) descr);
    array = (PyArrayObject *) PyArray_NewFromDescr(&PyArray_Type, descr, ndim,
                                                   shape, strides, arystruct->data,
                                                   flags, (PyObject *) miobj);

    if (array == NULL)
        return NULL;

    /* Set writable */
#if NPY_API_VERSION >= 0x00000007
    if (writeable) {
        PyArray_ENABLEFLAGS(array, NPY_ARRAY_WRITEABLE);
    }
    else {
        PyArray_CLEARFLAGS(array, NPY_ARRAY_WRITEABLE);
    }
#else
    if (writeable) {
        array->flags |= NPY_WRITEABLE;
    }
    else {
        array->flags &= ~NPY_WRITEABLE;
    }
#endif

    if (miobj) {
        /* Set the MemInfoObject as the base object */
#if NPY_API_VERSION >= 0x00000007
        if (-1 == PyArray_SetBaseObject(array,
                                        (PyObject *) miobj))
        {
            Py_DECREF(array);
            Py_DECREF(miobj);
            return NULL;
        }
#else
        PyArray_BASE(array) = (PyObject *) miobj;
#endif

    }
    return (PyObject *) array;
}

static void
NRT_adapt_buffer_from_python(Py_buffer *buf, arystruct_t *arystruct)
{
    int i;
    npy_intp *p;

    if (buf->obj) {
        /* Allocate new MemInfo only if the buffer has a parent */
        arystruct->meminfo = meminfo_new_from_pyobject((void*)buf->buf, buf->obj);
    }
    arystruct->data = buf->buf;
    arystruct->itemsize = buf->itemsize;
    arystruct->parent = buf->obj;
    arystruct->nitems = 1;
    p = arystruct->shape_and_strides;
    for (i = 0; i < buf->ndim; i++, p++) {
        *p = buf->shape[i];
        arystruct->nitems *= buf->shape[i];
    }
    for (i = 0; i < buf->ndim; i++, p++) {
        *p = buf->strides[i];
    }


}

static void
NRT_incref(NRT_MemInfo* mi) {
    if (mi) {
        NRT_MemInfo_acquire(mi);
    }
}

static void
NRT_decref(NRT_MemInfo* mi) {
    if (mi) {
        NRT_MemInfo_release(mi);
    }
}

static PyMethodDef ext_methods[] = {
#define declmethod(func) { #func , ( PyCFunction )func , METH_VARARGS , NULL }
#define declmethod_noargs(func) { #func , ( PyCFunction )func , METH_NOARGS, NULL }
    declmethod_noargs(memsys_use_cpython_allocator),
    declmethod_noargs(memsys_shutdown),
    declmethod(memsys_set_atomic_inc_dec),
    declmethod(memsys_set_atomic_cas),
    declmethod_noargs(memsys_get_stats_alloc),
    declmethod_noargs(memsys_get_stats_free),
    declmethod_noargs(memsys_get_stats_mi_alloc),
    declmethod_noargs(memsys_get_stats_mi_free),
    declmethod(meminfo_new),
    declmethod(meminfo_alloc),
    declmethod(meminfo_alloc_safe),
    { NULL },
#undef declmethod
};



static PyObject *
build_c_helpers_dict(void)
{
    PyObject *dct = PyDict_New();
    if (dct == NULL)
        goto error;

#define _declpointer(name, value) do {                 \
    PyObject *o = PyLong_FromVoidPtr(value);           \
    if (o == NULL) goto error;                         \
    if (PyDict_SetItemString(dct, name, o)) {          \
        Py_DECREF(o);                                  \
        goto error;                                    \
    }                                                  \
    Py_DECREF(o);                                      \
} while (0)

#define declmethod(func) _declpointer(#func, &NRT_##func)

declmethod(adapt_ndarray_from_python);
declmethod(adapt_ndarray_to_python);
declmethod(adapt_buffer_from_python);
declmethod(incref);
declmethod(decref);
declmethod(MemInfo_alloc);
declmethod(MemInfo_alloc_safe);
declmethod(MemInfo_alloc_aligned);
declmethod(MemInfo_alloc_safe_aligned);
declmethod(MemInfo_call_dtor);
declmethod(MemInfo_varsize_alloc);
declmethod(MemInfo_varsize_realloc);


#undef declmethod
    return dct;
error:
    Py_XDECREF(dct);
    return NULL;
}

MOD_INIT(_nrt_python) {
    PyObject *m;
    MOD_DEF(m, "_nrt_python", "No docs", ext_methods)
    if (m == NULL)
        return MOD_ERROR_VAL;
    import_array();
    NRT_MemSys_init();
    MemInfoType.tp_new = PyType_GenericNew;
    if (PyType_Ready(&MemInfoType))
        return MOD_ERROR_VAL;

    Py_INCREF(&MemInfoType);
    PyModule_AddObject(m, "_MemInfo", (PyObject *) (&MemInfoType));

    PyModule_AddObject(m, "c_helpers", build_c_helpers_dict());

    return MOD_SUCCESS_VAL(m);
}
