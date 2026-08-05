# Repository working agreement

- Never commit local credentials, generated databases, delivery artifacts, build outputs, `.env.runner`, `.env.backend`, or files under any `secrets/` directory.
- After completing and validating a requested code change, bump `VERSION` and every matching image/application version, commit the intended repository changes, and push `main` to `origin` unless the user explicitly asks not to publish.
- The `Build latest deployment package` workflow must remain green. It publishes the fixed release asset `autodev-hybrid-latest.zip` for server-side one-command updates.
- Preserve backward-compatible deployment: `deploy/backend/update-from-github.sh` must not overwrite `deploy/backend/.env.backend`, `deploy/backend/secrets/`, or `deploy/backend/data/`.
- Run `python -m unittest discover -s tests -v` before publishing. Validate modified PowerShell or shell scripts when the relevant runtime is available.
