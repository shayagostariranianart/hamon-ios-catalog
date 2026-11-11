# Hamon Film — iTunes → GitHub → WordPress bridge (v2.1)

Apple's public iTunes Search/Lookup APIs are read by GitHub Actions (outside Iran). Two delivery paths, usable together:

1. **Repo / pull mode (recommended for Iranian shared hosts).** The workflow writes `catalog/manifest.json` and small icons/screenshots to `catalog/img/` in this repository. WordPress (Publisher Panel 2.63.0+, menu **ناشرین بازی ← iTunes (دریافت از GitHub)**) pulls them every 30 minutes from `raw.githubusercontent.com` with automatic fallback to jsDelivr mirrors (or your own mirror URL). No inbound connection to the host is needed.
2. **Push mode.** The workflow POSTs to the REST endpoint of the panel (`HAMON_IMPORT_URL` + `HAMON_IMPORT_TOKEN` secrets). Requires the host to accept HTTPS from GitHub runners.

`SYNC_MODE` (repository variable): `auto` (default: both when the secrets exist, otherwise repo only), `repo`, `push`, `both`.

## Setup (repo mode)
1. Create a **public** GitHub repository with the contents of this folder at its root.
2. Settings → Actions → General → Workflow permissions → **Read and write permissions**.
3. Actions → *Sync iTunes iOS catalog* → Run workflow. It then runs daily.
4. In WordPress open **iTunes (دریافت از GitHub)**, enter `user/repo`, enable, press *آزمایش اتصال* then *اجرای فوری*.

Optional variables: `APPLE_COUNTRY` (default `us`), `APPLE_TERMS`, `REPO_MAX` (default 300), `SHOTS_PER_APP` (default 5), `FREE_ONLY`, `MAX_NEW_PER_RUN` (push mode).

## Notes
- Apple's Search API is query based; widen coverage with `APPLE_TERMS`.
- GitHub pauses scheduled workflows after 60 days without repository activity; this workflow commits catalog changes, which keeps it active. Re-enable from the Actions tab if needed.
- Apps are installed from Apple's App Store. Hamon only gates the outbound App Store link behind an active iOS subscription; no IPA files are hosted.
