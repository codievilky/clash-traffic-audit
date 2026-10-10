# Contributing

Use Python 3.9-compatible standard-library code. Keep the collector read-only toward Mihomo and keep runtime files out of Git.

```sh
python3 -m unittest discover -v
python3 test_clash_audit.py --capacity
node --check app.js
node test_ui.js
```

Accounting changes need synthetic cases for first samples, resets, gaps, closed connections and duplicate counting. Rerun the capacity test when changing retention, field lengths or cardinality limits. Never hide unattributed traffic or substitute sampled connection totals for the kernel total.

Bug reports should include versions, operating system, a minimal reproduction and redacted errors. Do not attach `.env`, proxy subscriptions, credentials, private hostnames, production databases or unsanitized exports.

Installer tests must mock `launchctl` and must not install or stop a real user's service. Fixtures use synthetic values and reserved domains such as `example.com`. English documentation contributions are welcome.
