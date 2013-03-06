import sys
import numpy as np
from ctypes import *

from numbapro._utils import finalizer

# enum curandStatus
## No errors
CURAND_STATUS_SUCCESS = 0
## Header file and linked library version do not match
CURAND_STATUS_VERSION_MISMATCH = 100
## Generator not initialized
CURAND_STATUS_NOT_INITIALIZED = 101
## Memory allocation failed
CURAND_STATUS_ALLOCATION_FAILED = 102
## Generator is wrong type
CURAND_STATUS_TYPE_ERROR = 103
## Argument out of range
CURAND_STATUS_OUT_OF_RANGE = 104
## Length requested is not a multple of dimension
CURAND_STATUS_LENGTH_NOT_MULTIPLE = 105
## GPU does not have double precision required by MRG32k3a
CURAND_STATUS_DOUBLE_PRECISION_REQUIRED = 106
## Kernel launch failure
CURAND_STATUS_LAUNCH_FAILURE = 201
## Preexisting failure on library entry
CURAND_STATUS_PREEXISTING_FAILURE = 202
## Initialization of CUDA failed
CURAND_STATUS_INITIALIZATION_FAILED = 203
## Architecture mismatch, GPU does not support requested feature
CURAND_STATUS_ARCH_MISMATCH = 204
## Internal library error
CURAND_STATUS_INTERNAL_ERROR = 999
curandStatus_t = c_int


# enum curandRngType
CURAND_RNG_TEST = 0
## Default pseudorandom generator
CURAND_RNG_PSEUDO_DEFAULT = 100
## XORWOW pseudorandom generator
CURAND_RNG_PSEUDO_XORWOW = 101
## MRG32k3a pseudorandom generator
CURAND_RNG_PSEUDO_MRG32K3A = 121
## Mersenne Twister pseudorandom generator
CURAND_RNG_PSEUDO_MTGP32 = 141
## Default quasirandom generator
CURAND_RNG_QUASI_DEFAULT = 200
## Sobol32 quasirandom generator
CURAND_RNG_QUASI_SOBOL32 = 201
## Scrambled Sobol32 quasirandom generator
CURAND_RNG_QUASI_SCRAMBLED_SOBOL32 = 202
## Sobol64 quasirandom generator
CURAND_RNG_QUASI_SOBOL64 = 203
## Scrambled Sobol64 quasirandom generator
CURAND_RNG_QUASI_SCRAMBLED_SOBOL64 = 204
curandRngType_t = c_int

# enum curandOrdering 
## Best ordering for pseudorandom results
CURAND_ORDERING_PSEUDO_BEST = 100
## Specific default 4096 thread sequence for pseudorandom results
CURAND_ORDERING_PSEUDO_DEFAULT = 101
## Specific seeding pattern for fast lower quality pseudorandom results
CURAND_ORDERING_PSEUDO_SEEDED = 102
## Specific n-dimensional ordering for quasirandom results
CURAND_ORDERING_QUASI_DEFAULT = 201
curandOrdering_t = c_int

# enum curandDirectionVectorSet
## Specific set of 32-bit direction vectors generated from polynomials
## recommended by S. Joe and F. Y. Kuo, for up to 20,000 dimensions
CURAND_DIRECTION_VECTORS_32_JOEKUO6 = 101
## Specific set of 32-bit direction vectors generated from polynomials
## recommended by S. Joe and F. Y. Kuo, for up to 20,000 dimensions,
## and scrambled
CURAND_SCRAMBLED_DIRECTION_VECTORS_32_JOEKUO6 = 102
## Specific set of 64-bit direction vectors generated from polynomials
## recommended by S. Joe and F. Y. Kuo, for up to 20,000 dimensions
CURAND_DIRECTION_VECTORS_64_JOEKUO6 = 103
## Specific set of 64-bit direction vectors generated from polynomials
## recommended by S. Joe and F. Y. Kuo, for up to 20,000 dimensions,
## and scrambled
CURAND_SCRAMBLED_DIRECTION_VECTORS_64_JOEKUO6 = 104
curandDirectionVectorSet_t = c_int

# enum curandMethod
CURAND_CHOOSE_BEST = 0
CURAND_ITR = 1
CURAND_KNUTH = 2
CURAND_HITR = 3
CURAND_M1 = 4
CURAND_M2 = 5
CURAND_BINARY_SEARCH = 6
CURAND_DISCRETE_GAUSS = 7
CURAND_REJECTION = 8
CURAND_DEVICE_API = 9
CURAND_FAST_REJECTION = 10
CURAND_3RD = 11
CURAND_DEFINITION = 12
CURAND_POISSON = 13
curandMethod_t = c_int


curandGenerator_t = c_void_p
p_curandGenerator_t = POINTER(curandGenerator_t)


class ctype_function(object):
    def __init__(self, restype=None, *argtypes):
        self.restype = restype
        self.argtypes = argtypes

class CuRandError(Exception):
    pass

class libcurand(object):
    __singleton = None

    def __new__(self, override_path=None):
        # Check if we already have opened the dll
        if self.__singleton is None:
            # No

            # Determine dll extension type for the platform
            if sys.platform == 'win32':
                dlext = '.dll'
                dllopener = WinDLL
            elif sys.platform == 'darwin':
                dlext = '.dylib'
                dllopener = CDLL
            else:
                dlext = '.so'
                dllopener = CDLL
            # Open the DLL
            path = 'libcurand' + dlext if not override_path else override_path
            dll = dllopener(path)

            # Create new instance
            inst = object.__new__(libcurand)
            self.__singleton = inst
            inst.dll = dll
            inst.__initialize()
        else:
            inst = self.__singleton
        return inst

    def __initialize(self):
        # Populate the instance with the functions
        for name, obj in vars(type(self)).items():
            if isinstance(obj, ctype_function):
                fn = getattr(self.dll, name)
                fn.restype = obj.restype
                fn.argtypes = obj.argtypes
                setattr(self, name, self._auto_checking_wrapper(fn))

    def _auto_checking_wrapper(self, fn):
        def wrapped(*args, **kws):
            status = fn(*args, **kws)
            self.check_error(status)
            return status
        return wrapped

    def check_error(self, status):
        if status != CURAND_STATUS_SUCCESS:
            raise CuRandError(status)

    @property
    def version(self):
        ver = c_int(0)
        self.curandGetVersion(byref(ver))
        return ver.value

    curandGetVersion = ctype_function(curandStatus_t, POINTER(c_int))

    curandCreateGenerator = ctype_function(
                                   curandStatus_t,
                                   p_curandGenerator_t, # generator reference
                                   curandRngType_t)     # rng_type

    curandDestroyGenerator = ctype_function(
                                    curandStatus_t,
                                    curandGenerator_t)
    
    curandSetPseudoRandomGeneratorSeed = ctype_function(
                                            curandStatus_t,
                                            curandGenerator_t,
                                            c_ulonglong)

    curandGenerateUniform = ctype_function(curandStatus_t,
                                           curandGenerator_t,
                                           POINTER(c_float),
                                           c_size_t)

    curandGenerateUniformDouble = ctype_function(curandStatus_t,
                                                 curandGenerator_t,
                                                 POINTER(c_double),
                                                 c_size_t)

    curandGenerateNormal = ctype_function(curandStatus_t,
                                          curandGenerator_t,
                                          POINTER(c_float),
                                          c_size_t,
                                          c_float,
                                          c_float)

    curandGenerateNormalDouble = ctype_function(curandStatus_t,
                                          curandGenerator_t,
                                          POINTER(c_double),
                                          c_size_t,
                                          c_double,
                                          c_double)

    curandGenerateLogNormal = ctype_function(curandStatus_t,
                                          curandGenerator_t,
                                          POINTER(c_float),
                                          c_size_t,
                                          c_float,
                                          c_float)

    curandGenerateLogNormalDouble = ctype_function(curandStatus_t,
                                                   curandGenerator_t,
                                                   POINTER(c_double),
                                                   c_size_t,
                                                   c_double,
                                                   c_double)


class Generator(finalizer.OwnerMixin):
    def __init__(self, rng_type=CURAND_RNG_TEST):
        self._api = libcurand()
        self._handle = curandGenerator_t(0)
        status = self._api.curandCreateGenerator(byref(self._handle), rng_type)
        self._finalizer_track(self._handle)

    @classmethod
    def _finalize(cls, handle):
        libcurand().curandDestroyGenerator(handle)

    def set_pseudo_random_generator_seed(self, seed):
        return self._api.curandSetPseudoRandomGeneratorSeed(self._handle, seed)

    def generate_uniform(self, devout, num):
        '''
        devout --- device array for the output
        num    --- # of float to generate
        '''
        fn, ptr = self.__float_or_double(devout,
                                         self._api.curandGenerateUniform,
                                         self._api.curandGenerateUniformDouble)
        return fn(self._handle, ptr, num)

    def generate_normal(self, devout, num, mean, stddev):
        fn, ptr = self.__float_or_double(devout,
                                         self._api.curandGenerateNormal,
                                         self._api.curandGenerateNormalDouble)
        return fn(self._handle, ptr, num)

    def generate_log_normal(self, devout, num, mean, stddev):
        fn, ptr = self.__float_or_double(
                                     devout,
                                     self._api.curandGenerateLogNormal,
                                     self._api.curandGenerateLogNormalDouble)
        return fn(self._handle, ptr, num)

    def __float_or_double(self, devary, floatfn, doublefn):
        if devary.dtype == np.float32:
            fn = self._api.curandGenerateUniform
            fty = c_float
        elif devary.dtype == np.float64:
            fn = self._api.curandGenerateUniformDouble
            fty = c_double
        else:
            raise ValueError("Only accept float or double arrays.")
        dptr = devary.device_raw_ptr.value
        ptr = cast(c_void_p(dptr), POINTER(fty))
        return fn, ptr