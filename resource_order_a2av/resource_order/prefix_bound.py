"""Experimental exact evaluator acceleration, not a new scheduling objective."""
import ctypes as c
import weakref


def prefix_evaluator(cover, costs, library):
    n = len(cover[0]) if cover else 0
    k = len(cover)
    if n == 0 or any(len(row) != n or any(v is not None and
             (type(v) is not int or not 0 <= v < n) for v in row) for row in cover):
        raise ValueError('invalid prefix cover')
    rows = (c.c_int*(n*k))(*(-1 if v is None else v for row in cover for v in row))
    times = (c.c_double*(3*n*n))(*(v for s in range(n) for d in range(n)
                                for v in costs.get((s,d),(0.,0.,-1.))))
    create = library.cover_prefix_create
    create.argtypes = [c.c_int,c.c_int,c.POINTER(c.c_int),c.POINTER(c.c_double)]
    create.restype = c.c_void_p
    destroy = library.cover_prefix_destroy
    destroy.argtypes = [c.c_void_p]
    destroy.restype = None
    run = library.cover_prefix_evaluate
    run.argtypes = [c.c_void_p,c.c_int,c.c_int,c.POINTER(c.c_ubyte),c.c_double]
    run.restype = c.c_double
    context = create(n,k,rows,times)
    if not context:
        raise MemoryError('could not allocate dependency prefix context')

    def evaluate(a,b,cycle,cutoff=float('inf')):
        if not cleanup.alive:
            raise ValueError('prefix evaluator is closed')
        cycle = tuple(cycle)
        if not 0 <= a < k or not 0 <= b < k or any(type(s) is not int or not 0 <= s < n for s in cycle):
            raise ValueError('invalid prefix exchange')
        mask = (c.c_ubyte*n)()
        for s in cycle:
            mask[s] = 1
        return run(context,a,b,mask,cutoff)

    cleanup = weakref.finalize(evaluate,destroy,context)
    evaluate.close = cleanup
    return evaluate
