#!/bin/bash

set -u

PASTA="$(cd "$(dirname "$0")" && pwd)"
PROGRAMA="$PASTA/programa"
VENV="$PROGRAMA/.venv-mac"
VPY="$VENV/bin/python"
TOOLS="$PROGRAMA/.tools"
UV="$TOOLS/uv"
PYTHONS="$PROGRAMA/.python"

clear
echo "GW APPS EDITION VIDEOS — macOS"
echo

if [ ! -f "$PROGRAMA/app.py" ]; then
  echo "Não achei a pasta 'programa' ao lado deste arquivo."
  echo "Mantenha este iniciador dentro da pasta GW Apps Edition Videos."
  echo
  read -r -p "Pressione Enter para fechar."
  exit 1
fi

cd "$PROGRAMA" || exit 1

if [ ! -x "$VPY" ] || ! "$VPY" -c 'import numpy, PIL, sherpa_onnx, static_ffmpeg' >/dev/null 2>&1; then
  echo "Primeira abertura: preparando o aplicativo."
  echo "A instalação e o leitor de fala usam cerca de 600 MB e podem levar alguns minutos."
  echo
  if [ ! -x "$UV" ]; then
    echo "Baixando o instalador interno..."
    if ! curl -LsSf https://astral.sh/uv/install.sh | env UV_UNMANAGED_INSTALL="$TOOLS" sh; then
      echo "Não foi possível baixar o instalador interno. Confira sua internet."
      read -r -p "Pressione Enter para fechar."
      exit 1
    fi
  fi
  env UV_PYTHON_INSTALL_DIR="$PYTHONS" "$UV" python install 3.12 || {
    echo "Não foi possível preparar o Python interno."
    read -r -p "Pressione Enter para fechar."
    exit 1
  }
  env UV_PYTHON_INSTALL_DIR="$PYTHONS" "$UV" venv --python 3.12 --clear "$VENV" || {
    echo "Não foi possível criar o ambiente do aplicativo."
    read -r -p "Pressione Enter para fechar."
    exit 1
  }
  "$UV" pip install --python "$VPY" -r requirements.txt || {
    echo
    echo "Falhou ao instalar as dependências. Confira sua internet e tente novamente."
    read -r -p "Pressione Enter para fechar."
    exit 1
  }
fi

if ! "$VPY" -c 'import editor; editor.ffbin("ffmpeg"); editor.baixar_modelo(); editor.verificar()'; then
  echo
  echo "Falhou ao preparar o editor. Confira sua internet e tente novamente."
  read -r -p "Pressione Enter para fechar."
  exit 1
fi

echo
echo "Abrindo o aplicativo no navegador..."
echo "Deixe esta janela aberta enquanto estiver usando o editor."
echo
exec "$VPY" app.py
