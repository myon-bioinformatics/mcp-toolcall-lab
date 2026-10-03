# Python CI JUnit compatibility

The primary Python job in `.github/workflows/test.yml` still installs
`.[test]` and runs the same pytest suite. It adds
`--junitxml=reports/pytest.xml` and uploads that exact file as
`junit-python-3.11` for 14 days with `if: always()`. The pytest step
has no error suppression: a failure remains a failed producer job and run.

The dependent `junit-identity` job runs even after producer failure
when change detection selected non-document work. It calls the parent
repository's reusable collector at
`4dfda95d6573250477f991a0421fa6acb9bc0258`, with the exact report set
`["junit-python-3.11/pytest.xml"]`. Missing or invalid reports fail collection;
a successful collection only means evidence is complete, not that tests passed.
Documentation-only runs retain the existing skip behavior.

Raw XML remains an Actions artifact only; it is never added to Pages.
The compact `python-failure-identity` artifact contains collection evidence
and bounded failure/error identities, without messages, traceback or parameter
values. Canonical commit identity is unavailable in this consumer, so its
`commit_sha` stays null. This is the JUnit compatibility adapter tracked in
[the parent Issue #22](https://github.com/myon-bioinformatics/myon-bioinformatics/issues/22),
not completion of native JSONL/reproduction or cross-repository learning.

Deno, public-resolver compatibility, Docker/browser workflows and runtime
dependencies keep their existing boundaries. Regression tests invoke real
child pytest runs with and without JUnit on passing and failure/setup-error/skip
suites, compare exit codes, and check the failure-preservation wiring.

The producer runs after the shared vendor snapshot has been downloaded and
verified. Vendor lock evidence and raw JUnit remain separate Actions artifacts.
Controlled child pytest uses an explicit temporary root so JUnit retains its
`test_child` module identity even with `-c os.devnull`.
