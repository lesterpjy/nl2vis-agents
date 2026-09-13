"""The settings both agents run at, in one place because determinism is a property of the pair, not of either one.

Both ran at the provider's default temperature until now, which for a benchmark is indefensible: re-running the same
configuration over the same 100 VisEval cases moved 3 correctness cases and 8 form cases. Zero temperature and a fixed
seed are as close to repeatable as the provider offers — OpenAI does not promise bit-determinism, and `system_fingerprint` can
change under us — so the residual is measured and reported rather than assumed away.
"""

from pydantic_ai.settings import ModelSettings

SEED = 11
# A liveness bound, not a latency budget: a call takes 1.5 to 3 seconds, and a request that never returns once held a run for
# seven hours, taking 151 finished cases with it. Generous, so it only ever turns a hang into one failed case.
TIMEOUT_S = 120.0

SETTINGS = ModelSettings(temperature=0.0, seed=SEED, timeout=TIMEOUT_S)
