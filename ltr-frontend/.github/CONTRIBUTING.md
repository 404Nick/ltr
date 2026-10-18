# How to contribute

You're free to submit bugs, suggestions, pull requests, and any other kind of contribution.

## Before you start

Before we can accept your code and make it part of the project, please read this short introduction. This isn't a tutorial or instruction manual — it's just an overview to help with getting started!

### Installing packages on Arch Linux:
```bash
sudo pacman -S --needed python wl-clipboard      # wl-clipboard или xclip — для копирования/вставки
python -m venv ~/.local/share/ltr-venv
~/.local/share/ltr-venv/bin/pip install argostranslate textual
install -Dm755 ltr ~/.local/bin/ltr
sed -i '1s|.*|#!'"$HOME"'/.local/share/ltr-venv/bin/python|' ~/.local/bin/ltr
```

## Code Style and Guidelines

The goal is to use [flake8](https://www.flake8.org/) for linting and [isort](https://pycqa.github.io/isort/)/[black](https://black.readthedocs.io/en/stable/) for formatting code. See the `tox.ini` and `setup.py` for details on these tools.

## Contributing

We welcome contributions to this project, however please adhere to our code style guidelines mentioned above. Make sure to run `make lint` to check your changes before submitting a pull request.
