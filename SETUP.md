# Remote Tmux Manager - Standalone Package

This is a standalone, reusable package extracted from the Chrono-DSA project.

## Quick Setup

```bash
# 1. Initialize git repository (already done)
cd /Users/initialmoon/Documents/PhD/chrono期刊/remote-tmux-manager
git add .
git commit -m "Initial commit: Remote tmux manager v0.1.0"

# 2. Create GitHub repository
# Go to https://github.com/new
# Repository name: remote-tmux-manager
# Description: AI-friendly remote tmux session manager
# Public or Private: Your choice
# Do NOT initialize with README (we already have one)

# 3. Push to GitHub
git remote add origin https://github.com/yourusername/remote-tmux-manager.git
git branch -M main
git push -u origin main

# 4. Test installation
pip install -e .
remote-tmux --help
```

## Using in Chrono-DSA

```bash
# In chrono-dsa directory
cd /Users/initialmoon/Documents/PhD/chrono期刊/chrono-dsa

# Add as submodule
git submodule add https://github.com/yourusername/remote-tmux-manager.git external/remote-tmux-manager

# Install
uv pip install -e external/remote-tmux-manager

# Update main.py to import from package
# Change: from scripts.remote_tmux.cli import add_remote_subparser
# To: from remote_tmux.cli import add_remote_subparser
```

## Package Structure

```
remote-tmux-manager/
├── .git/                       # Git repository
├── .gitignore                  # Ignore patterns
├── LICENSE                     # MIT License
├── README.md                   # Main documentation
├── pyproject.toml              # Package configuration
├── remote_tmux/                # Main package
│   ├── __init__.py            # Package exports
│   ├── config.py              # Profile configuration
│   ├── manager.py             # Tmux session manager
│   ├── cli.py                 # CLI interface
│   └── py.typed               # Type checking marker
├── tests/                      # Test suite
│   ├── __init__.py
│   └── test_remote_tmux.py    # Unit tests
├── examples/                   # Example configs
│   └── profiles.example.yaml
└── docs/                       # Documentation
    └── INTEGRATION.md          # Integration guide
```

## Next Steps

1. Create GitHub repository
2. Push code to GitHub
3. Add as submodule to chrono-dsa
4. Update chrono-dsa imports
5. (Optional) Publish to PyPI

## Testing

```bash
# Run tests
python -m unittest discover -s tests -p "test_*.py" -v

# Install and test CLI
pip install -e .
remote-tmux --help
```

## Publishing to PyPI (Optional)

```bash
# Install build tools
pip install build twine

# Build package
python -m build

# Upload to PyPI
twine upload dist/*
```
