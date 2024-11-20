curl -L \
  -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer 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" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  https://api.github.com/repos/marcossilva/hotel-chilli/actions/workflows/github-actions.yml/dispatches \
  -d '{"ref":"master"}'