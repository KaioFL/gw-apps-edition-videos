#!/bin/bash
# Gera o aplicativo do Mac (.app) e o instalador (.dmg). Precisa rodar num Mac.
# Uso (Terminal, na pasta desktop):   bash build_mac.sh
# Resultado: desktop/saida/GW-Apps-Edition-Videos-<versão>-Mac-<AppleSilicon|Intel>.dmg
#
# Opcional (com conta Apple Developer, tira o aviso de "desenvolvedor não identificado"):
#   GW_MAC_ASSINATURA="Developer ID Application: Sua Empresa (TEAMID)"
#   GW_MAC_NOTARIZAR_PERFIL="perfil-do-notarytool"   (criado com: xcrun notarytool store-credentials)
set -euo pipefail
cd "$(dirname "$0")"
NOME="GW Apps Edition Videos"
PY="${GW_PYTHON:-python3}"

if [ ! -x .venv-build/bin/python ]; then
  echo "Criando o ambiente de build..."
  "$PY" -m venv .venv-build
fi
VPY=.venv-build/bin/python
echo "[1/5] Instalando as ferramentas de build..."
"$VPY" -m pip install --disable-pip-version-check -q --upgrade pip
"$VPY" -m pip install --disable-pip-version-check -q -r requisitos-build.txt

echo "[2/5] Juntando ffmpeg, leitor de fala e ícones..."
"$VPY" preparar_build.py
# .icns com todos os tamanhos pelo iconutil do próprio Mac
rm -rf build/icone.iconset && mkdir -p build/icone.iconset
for t in 16 32 128 256 512; do
  sips -z $t $t recursos/icone.png --out "build/icone.iconset/icon_${t}x${t}.png" >/dev/null
  sips -z $((t * 2)) $((t * 2)) recursos/icone.png --out "build/icone.iconset/icon_${t}x${t}@2x.png" >/dev/null
done
iconutil -c icns build/icone.iconset -o recursos/icone.icns

echo "[3/5] Montando o aplicativo..."
"$VPY" -m PyInstaller --noconfirm --clean gw_desktop.spec --distpath dist --workpath build
APP="dist/$NOME.app"

echo "[4/5] Assinando..."
if [ -n "${GW_MAC_ASSINATURA:-}" ]; then
  codesign --force --deep --options runtime --timestamp --entitlements entitlements.plist \
    -s "$GW_MAC_ASSINATURA" "$APP"
else
  codesign --force --deep -s - "$APP"      # assinatura local (ad-hoc): obrigatória nos Macs com chip Apple
fi
codesign --verify --deep "$APP"

echo "[5/5] Gerando o .dmg..."
VERSAO=$(sed -n 's/^VERSAO = "\([^"]*\)".*/\1/p' ../programa/app.py)
[ "$(uname -m)" = "arm64" ] && ROTULO="AppleSilicon" || ROTULO="Intel"
DMG="saida/GW-Apps-Edition-Videos-$VERSAO-Mac-$ROTULO.dmg"
mkdir -p saida
PALCO=build/dmg
rm -rf "$PALCO" && mkdir -p "$PALCO"
cp -R "$APP" "$PALCO/"
ln -s /Applications "$PALCO/Aplicativos"
cp "LEIA PRIMEIRO - Mac.txt" "$PALCO/" 2>/dev/null || true
cp "../COMO USAR.txt" "$PALCO/" 2>/dev/null || true
rm -f "$DMG"
hdiutil create -volname "$NOME" -srcfolder "$PALCO" -ov -format UDZO "$DMG" >/dev/null

if [ -n "${GW_MAC_ASSINATURA:-}" ]; then
  codesign --force -s "$GW_MAC_ASSINATURA" "$DMG"
  if [ -n "${GW_MAC_NOTARIZAR_PERFIL:-}" ]; then
    xcrun notarytool submit "$DMG" --keychain-profile "$GW_MAC_NOTARIZAR_PERFIL" --wait
    xcrun stapler staple "$DMG"
  fi
fi
echo "Pronto: $DMG"
