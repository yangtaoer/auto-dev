# Repository working agreement

- Never commit local credentials, generated databases, delivery artifacts, build outputs, `.env.runner`, `.env.backend`, or files under any `secrets/` directory.
- After completing and validating a requested code change, bump `VERSION` and every matching image/application version, commit the intended repository changes, push `main` to `origin`, then run `scripts/publish-github.ps1` unless the user explicitly asks not to publish.
- `scripts/publish-github.ps1` publishes the fixed release asset `autodev-hybrid-latest.zip` for server-side one-command updates. The GitHub Actions workflow is a manual fallback because this account may not currently start hosted runners.
- Preserve backward-compatible deployment: `deploy/backend/update-from-github.sh` must not overwrite `deploy/backend/.env.backend`, `deploy/backend/secrets/`, or `deploy/backend/data/`.
- Run `python -m unittest discover -s tests -v` before publishing. Validate modified PowerShell or shell scripts when the relevant runtime is available.

## Project testing knowledge

- When the user supplements project testing accounts, shift positions, unit jurisdiction, regional differences, or regression experience, read `.agents/skills/project-test-knowledge/SKILL.md` and persist the scoped facts in its references. Keep local credentials separate from versioned knowledge.
- Consult this knowledge skill before planning or implementing project automated testing. Knowledge maintenance does not authorize deployment, shift handover, or business-data changes.
- The user requests ongoing GitHub synchronization of these knowledge additions: validate and commit only the intended knowledge changes, then push to the verified repository remote. Preserve unrelated working-tree changes.
