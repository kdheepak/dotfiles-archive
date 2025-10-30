#!/usr/bin/env bash
echo "Symlinking files from $(pwd)"
mkdir -p ~/.config
mkdir -p ~/.julia/config
mkdir -p ~/local/bin
mkdir -p ~/.config/tmux

rm ~/.gitignore
ln -s "$(pwd)/.gitignore" ~/.gitignore

rm ~/local/bin/preview_file_or_folder
ln -s "$(pwd)/preview_file_or_folder" ~/local/bin/preview_file_or_folder
rm ~/.config/direnv/direnvrc
mkdir -p ~/.config/direnv
ln -s "$(pwd)/direnvrc" ~/.config/direnv/direnvrc
rm ~/.config/.exports
ln -s "$(pwd)/exports" ~/.config/.exports
rm ~/.config/.aliases
ln -s "$(pwd)/aliases" ~/.config/.aliases
rm ~/Library/ApplicationSupport/jesseduffield/lazygit/config.yml
mkdir -p ~/Library/ApplicationSupport/jesseduffield/lazygit/
ln -s "$(pwd)/lazygit.yml" ~/Library/ApplicationSupport/jesseduffield/lazygit/config.yml
rm ~/.bash_profile
ln -s "$(pwd)/bash_profile" ~/.bash_profile
rm ~/.zshenv
ln -s "$(pwd)/zshenv" ~/.zshenv
rm ~/.prettierrc
ln -s "$(pwd)/prettierrc" ~/.prettierrc
rm ~/.zshrc
ln -s "$(pwd)/zshrc" ~/.zshrc
rm ~/.zprofile
ln -s "$(pwd)/zprofile" ~/.zprofile
rm ~/.config/starship.toml
ln -s "$(pwd)/starship.toml" ~/.config/starship.toml
# rm ~/.rustfmt.toml
# ln -s "$(pwd)/rustfmt.toml" ~/.rustfmt.toml
rm ~/.condarc
ln -s "$(pwd)/condarc" ~/.condarc
rm ~/.config/flake8
ln -s "$(pwd)/flake8" ~/.config/flake8
rm ~/.config/pycodestyle
ln -s "$(pwd)/pycodestyle" ~/.config/pycodestyle
rm ~/.config/.fzf.bash
ln -s "$(pwd)/fzf.bash" ~/.config/.fzf.bash
rm ~/.config/.fzf.zsh
ln -s "$(pwd)/fzf.zsh" ~/.config/.fzf.zsh
rm ~/.gdbinit
ln -s "$(pwd)/gdbinit" ~/.gdbinit
rm ~/.gitconfig
ln -s "$(pwd)/gitconfig" ~/.gitconfig
rm ~/.julia/config/startup.jl
ln -s "$(pwd)/juliarc.jl" ~/.julia/config/startup.jl
rm -rf ~/.config/bat
ln -s "$(pwd)/bat" ~/.config/bat
rm -rf ~/local/bin/irg
ln -s "$(pwd)/fzf/irg" ~/local/bin/irg
rm -rf ~/.config/alacritty
ln -s "$(pwd)/alacritty" ~/.config/alacritty
rm -rf ~/.config/zellij
ln -s "$(pwd)/zellij" ~/.config/zellij
rm -rf ~/.config/wezterm
ln -s "$(pwd)/wezterm" ~/.config/wezterm
rm -rf ~/.config/tmux/tmux.conf
ln -s "$(pwd)/tmux.conf" ~/.config/tmux/tmux.conf
rm -rf ~/mise
rm -rf ~/.config/zed
ln -s "$(pwd)/zed" ~/.config/zed
rm -rf ~/.config/jj
ln -s "$(pwd)/jj" ~/.config/jj

rm -rf ~/local/bin/git-ilog
ln -s "$(pwd)/scripts/git-ilog" ~/local/bin/git-ilog
rm -rf ~/local/bin/run-with-timeout
ln -s "$(pwd)/scripts/run-with-timeout.py" ~/local/bin/run-with-timeout

mkdir -p ~/.config/just
rm ~/.config/just/justfile
ln -s "$(pwd)/justfile" ~/.config/just/justfile

rm ~/.Brewfile
ln -s "$(pwd)/Brewfile" ~/.Brewfile
