import numpy as np
from .thread_pool import ThreadPool
from .gbs import GBS


class ExtrapolationStepper(object):
    def __init__(self, steppers, steps, weights, num_cores=1):
        """Initialize the ExtrapolationStepper
           :param steppers: list of underlying time steppers
           :param steps: step counts for each stepper in the extrapolation scheme
           :param weights: weights for each stepper in the scheme
           :param num_cores: number of cores on which to evaluate the scheme
        """
        if len(steppers) != len(steps) or len(steppers) != len(weights):
            raise ValueError('number of steppers, step counts, and weights must all match!')

        # Sort the time steppers by step counts
        indices = np.argsort(steps)
        self._steppers = [steppers[index] for index in indices]
        self._steps = np.array(steps)[indices]
        self._weights = np.array(weights)[indices]

        self._num_cores = num_cores
        if num_cores > 1:
            self._evalfn = self._evaluate_parallel
            self._initialize_threads(num_cores)
        else:
            self._evalfn = self._evaluate_serial
            self._pool = None

    def __del__(self):
        self.join()

    def join(self):
        """Block while waiting for all threads in the pool to join.
        """
        if self._pool:
            self._pool.join()

    def step(self, system, state, t, dt, n):
        """Time step the extrapolation scheme n times, returning output from each time point.
           :param system: callable ODE to time step, where y\'=system(t,state)
           :param state: state of the system
           :param t: time of the evaluation
           :param dt: time step size
           :param n: number of time steps
        """
        evalfn  = self._evalfn
        weights = self._weights
        output  = np.empty((n, *np.shape(state)))
        for ii in range(n):
            results    = evalfn(system, state, t, dt)
            state      = np.dot(weights, results)
            output[ii] = state
            t          = t+dt
        return output

    def _evaluate_serial(self, system, state, t, dt):
        """Evaluate the time steppers in the current thread."""
        return [stepper.step(system, state, t, dt) for stepper in self._steppers]

    def _evaluate_parallel(self, system, state, t, dt):
        """Evaluate the time steppers in parallel across the pool."""
        # Set the arguments to the stepper calls
        self._pool.set_args('all', (system, state, t, dt))

        # Notify the threads to process
        self._pool.notify()

        # Access the pool data, blocking until synchronized
        data = self._pool.data()

        # Merge the thread worker results into a single array
        results = [None]*len(self._steppers)
        for ii in range(len(data)):
            inds = data[ii][0]
            outs = data[ii][1]
            for jj in range(len(inds)):
                results[inds[jj]] = outs[jj]
        return results

    def _initialize_threads(self, num_cores):
        """Initialize the thread pool, balancing the load across each thread."""
        fns = [stepper.step for stepper in self._steppers]
        num_steppers = len(self._steppers)
        if num_steppers % num_cores != 0:
            raise ValueError('For now, number of steppers must be a multiple of the number of cores')

        steppers_per_core = int(num_steppers/num_cores)
        if steppers_per_core % 2 != 0:
            raise ValueError('For now, number of steppers per core must be even')

        def make_worker_target_fn(ii):
            sps2 = int(steppers_per_core/2)
            ind1 =  ii   *sps2
            ind2 = (ii+1)*sps2
            ind3 = num_steppers-(ii+1)*sps2
            ind4 = num_steppers- ii   *sps2
            inds = list(range(ind1,ind2))+list(range(ind3,ind4))
            def eval(*args):
                results = [self._steppers[inds[jj]].step(*args) for jj in range(steppers_per_core)]
                return inds, results
            return eval

        fns = [make_worker_target_fn(ii) for ii in range(num_cores)]
        self._pool = ThreadPool(fns)

