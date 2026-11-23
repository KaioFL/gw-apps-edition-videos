"""Gera os ícones do aplicativo a partir de programa/marca/logo.png.

Saída em desktop/recursos: icone.png (1024), icone.ico (Windows) e icone.icns (Mac).
Uso: python gerar_icones.py
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFilter

AQUI = os.path.dirname(os.path.abspath(__file__))
LOGO = os.path.join(AQUI, "..", "programa", "marca", "logo.png")
SAIDA = os.path.join(AQUI, "recursos")


def icone(lado=1024, margem=0.098):
    """Quadrado arredondado escuro (mesmo fundo do painel) com o símbolo GW no meio."""
    S = lado * 4                       # desenha grande e reduz: borda lisa
    m = int(S * margem)                # 0.098 = padrão dos ícones do Mac (824 de 1024)
    r = int((S - 2 * m) * 0.225)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    sombra = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(sombra).rounded_rectangle((m, m + S // 90, S - m, S - m + S // 90), r, fill=(0, 0, 0, 110))
    img.alpha_composite(sombra.filter(ImageFilter.GaussianBlur(S // 70)))

    fundo = Image.new("RGBA", (S, S))
    topo, base = (34, 26, 48), (12, 9, 18)          # #221a30 -> #0c0912
    px = ImageDraw.Draw(fundo)
    for y in range(S):
        t = y / (S - 1)
        px.line([(0, y), (S, y)], fill=tuple(int(topo[i] + (base[i] - topo[i]) * t) for i in range(3)) + (255,))
    mascara = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mascara).rounded_rectangle((m, m, S - m, S - m), r, fill=255)
    img.paste(fundo, (0, 0), mascara)

    borda = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(borda).rounded_rectangle((m, m, S - m, S - m), r, outline=(139, 92, 246, 90), width=max(2, S // 256))
    img.alpha_composite(borda)

    logo = Image.open(LOGO).convert("RGBA")
    larg = int((S - 2 * m) * 0.78)
    alt = int(logo.height * larg / logo.width)
    logo = logo.resize((larg, alt), Image.LANCZOS)
    img.alpha_composite(logo, ((S - larg) // 2, (S - alt) // 2))
    return img.resize((lado, lado), Image.LANCZOS)


def main():
    os.makedirs(SAIDA, exist_ok=True)
    grande = icone(1024)
    grande.save(os.path.join(SAIDA, "icone.png"))
    icone(1024, margem=0.02).save(os.path.join(SAIDA, "icone.ico"),
                sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    try:
        grande.save(os.path.join(SAIDA, "icone.icns"))
    except Exception as ex:   # o build do Mac refaz com o iconutil se precisar
        print("Aviso: não gerei o .icns aqui:", ex, file=sys.stderr)
    print("Ícones gerados em", SAIDA)


if __name__ == "__main__":
    main()
