def _small(diffsize: Scalar) -> bool:
  # TODO(kidger): make a more careful choice here -- the existence of this
  # function is pretty ad-hoc.
  resolution = 10 ** (2 - jnp.finfo(diffsize.dtype).precision)
  return diffsize < resolution


def _diverged(rate: Scalar) -> bool:
  return ~jnp.isfinite(rate) | (rate > 2)


def _converged(factor: Scalar, tol: Scalar) -> bool:
  return (factor > 0) & (factor < tol)


class _Damped(eqx.Module):
  fn: Callable
  damping: float

  def __call__(self, y, args):
    damp = jnp.sqrt(self.damping)
    return self.fn(y, args), jtu.tree_map(lambda x: damp * x, y)


class _GaussNewtonLevenbergMarquardtState(eqx.Module):
  step: Scalar
  diffsize: Scalar
  diffsize_prev: Scalar


class _GaussNewtonLevenbergMarquardt(AbstractLeastSquaresSolver):
  rtol: float
  atol: float
  kappa: float = 1e-2
  norm: Callable = rms_norm
  linear_solver: AbstractLinearSolver = AutoLinearSolver()

  @property
  @abc.abstractmethod
  def _is_gauss_newton(self) -> bool:
    ...

  def init(self, residual_prob, y, args, options):
    del residual_prob, y, args, options
    return _GaussNewtonLevenbergMarquardtState(step=jnp.array(0), diffsize=jnp.array(0.0), diffsize_prev=jnp.array(0.0))

  def step(self, residual_prob, y, args, options, state):
    del options
    residuals = residual_prob.fn(y, args)
    if self._is_gauss_newton:
      matrix = JacobianLinearOperator(residual_prob.fn, y, args)
    else:
      damping = # TODO(kidger)
      matrix = JacobianLinearOperator(_Damped(residual_prob.fn, damping), y, args)
      residuals = (residuals, jtu.tree_map(jnp.zeros_like, y))
    diff = linear_solve(matrix, residuals, self.linear_solver).solution
    # Yep, no `J^T J` here.
    #
    # So Gauss-Newton is often defined as `diff = (J^T J)^{-1} J^T r`.
    # (Similarly for Levenberg-Marquardt.)
    #
    # However `(J^T J)^{-1} J^T` is the pseudoinverse of `J`, i.e. the solution to a
    # linear least squares problem. When folks write down this `J^T J` version, what
    # they're actually doing is just solving this linear least squares problem via
    # the normal equations. (Which reduce linear least squares down to a linear
    # solve.)
    #
    # However this is generally not a great idea:
    # - Calling `J^T J` squares the condition number => bad for numerical stability;
    # - This can take a long time to compile: JAX isn't smart enough to treat each
    #   `J` and `J^T` together, and treats each as a separate autodiff call. (Grr,
    #   the endless problem with XLA inlining everything.)
    #
    # Much better to just "solve" `diff = J^{-1} r` directly: our `linear_solve`
    # routine will return a linear least squares solution in general (if the matrix
    # is singular).
    #
    # ...and then, if you wish, take `linear_solver=CG(normal=True)` to solve it via
    # the normal equations in the textbook way! (In practice if you go looking
    # around, you'll see that most sophisticated implementations actually solve this
    # using a QR decomposition.)
    scale = (self.atol + self.rtol * y**ω).ω
    diffsize = self.norm((diff**ω / scale**ω).ω)
    new_y = (y**ω - diff**ω).ω
    new_state = _GaussNewtonLevenbergMarquardtState(step=state.step + 1, diffsize=diffsize, diffsize_prev=state.diffsize)
    return new_y, new_state

  def terminate(self, residual_prob, y, args, options, state):
    del residual_prob, y, args, options
    at_least_two = state.step >= 2
    rate = state.diffsize / state.diffsize_prev
    factor = state.diffsize * rate / (1  - rate)
    small = _small(state.diffsize)
    diverged = _diverged(rate)
    converged = _converged(factor, self.kappa)
    terminate = at_least_two & (small | diverged | converged)
    result = jnp.where(converged, RESULTS.successful, RESULTS.nonconvergence)
    result = jnp.where(diverged, RESULTS.divergence, result)
    result = jnp.where(small, RESULTS.successful, result)
    return terminate, result


class GaussNewton(_GaussNewtonLevenbergMarquardt):
  _is_gauss_newton = True


class LevenbergMarquardt(_GaussNewtonLevenbergMarquardt):
  _is_gauss_newton = False