# Publishing to GitHub

This release folder is already a clean Git repository with one commit.

## One-Time Login

Run this in PowerShell:

```powershell
& "C:\Program Files\GitHub CLI\gh.exe" auth login
```

Choose:

- GitHub.com
- HTTPS
- Authenticate with browser

## Create and Push New Repository

From this folder:

```powershell
cd "C:\Users\mishi\OneDrive\Documents\New project\king-of-codex-release"
& "C:\Program Files\GitHub CLI\gh.exe" repo create king-of-codex --public --source . --remote origin --push
```

For a private repository, replace `--public` with `--private`.

## Existing Remote Alternative

If you create the repository manually on GitHub, push with:

```powershell
git remote add origin https://github.com/<your-user>/king-of-codex.git
git branch -M main
git push -u origin main
```
