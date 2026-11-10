"""Validação e conversão de uma fotografia, sem acesso a disco ou ao Storage."""

from dataclasses import dataclass
from io import BytesIO
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.datastructures import FileStorage


MAX_ORIGINAL_BYTES = 3 * 1024 * 1024
MAX_WEBP_BYTES = 512_000
MAX_PIXELS = 20_000_000
MAX_LADO_ORIGINAL = 10_000
MAX_LADO_FINAL = 1600
MIN_LADO_REDUCAO = 320
QUALIDADES = (85, 75, 65, 55, 45)


class FotoInvalida(ValueError):
    """Erro de fotografia cuja mensagem pode ser apresentada no formulário."""


@dataclass(frozen=True)
class FotoProcessada:
    conteudo: bytes
    largura: int
    altura: int
    content_type: str = "image/webp"


def processar_foto(arquivo: FileStorage) -> FotoProcessada:
    """Consome o stream do upload e retorna WebP estático, sem metadados.

    O chamador continua responsável pelo upload e por limitar o corpo HTTP e
    a quantidade de fotos. Nome, MIME e content_length enviados são ignorados.
    """
    try:
        original = arquivo.read(MAX_ORIGINAL_BYTES + 1)
        if not original:
            raise FotoInvalida("Selecione uma fotografia que não esteja vazia.")
        if len(original) > MAX_ORIGINAL_BYTES:
            raise FotoInvalida("A fotografia original deve ter no máximo 3 MiB.")

        # Não altera Image.MAX_IMAGE_PIXELS nem outras opções globais do Pillow.
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(original)) as foto:
                if foto.format not in {"JPEG", "PNG", "WEBP"}:
                    raise FotoInvalida("Envie uma fotografia JPEG, PNG ou WebP.")
                largura, altura = foto.size
                if (largura * altura > MAX_PIXELS
                        or max(largura, altura) > MAX_LADO_ORIGINAL):
                    raise FotoInvalida("A fotografia tem dimensões excessivas. Use até "
                                       "20 megapixels e 10.000 pixels por lado.")
                if getattr(foto, "is_animated", False):
                    raise FotoInvalida("Imagens animadas não são aceitas. Envie uma fotografia estática.")
                foto.verify()

            # verify() invalida o objeto: reabre e força a decodificação completa.
            with Image.open(BytesIO(original)) as foto:
                foto.load()
                with ImageOps.exif_transpose(foto) as orientada:
                    modo = "RGBA" if "A" in orientada.getbands() or "transparency" in orientada.info else "RGB"
                    with orientada.convert(modo) as preparada:
                        preparada.info.clear()
                        preparada.thumbnail((MAX_LADO_FINAL, MAX_LADO_FINAL), Image.Resampling.LANCZOS)
                        return _codificar_webp(preparada)
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as erro:
        raise FotoInvalida("A fotografia tem dimensões excessivas. Escolha uma imagem menor.") from erro
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, EOFError) as erro:
        if isinstance(erro, FotoInvalida):
            raise
        raise FotoInvalida("Não foi possível ler a fotografia. Envie uma imagem JPEG, PNG ou WebP válida.") from erro


def _codificar_webp(foto: Image.Image) -> FotoProcessada:
    while True:
        for qualidade in QUALIDADES:
            with BytesIO() as saida:
                foto.save(saida, format="WEBP", quality=qualidade, method=4)
                if saida.tell() <= MAX_WEBP_BYTES:
                    return FotoProcessada(saida.getvalue(), foto.width, foto.height)

        maior_lado = max(foto.size)
        if maior_lado <= MIN_LADO_REDUCAO:
            raise FotoInvalida("Não foi possível reduzir a fotografia para o tamanho permitido. "
                               "Escolha outra imagem.")
        novo_lado = max(MIN_LADO_REDUCAO, int(maior_lado * 0.8))
        foto.thumbnail((novo_lado, novo_lado), Image.Resampling.LANCZOS)


def criar_miniatura(conteudo: bytes) -> bytes:
    """Reduz o WebP armazenado a 192 px, em memória e sem metadados."""
    if len(conteudo) > MAX_WEBP_BYTES:
        raise FotoInvalida("Fotografia indisponível.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(conteudo)) as foto:
                if (foto.format != "WEBP" or max(foto.size) > MAX_LADO_FINAL
                        or getattr(foto, "is_animated", False)):
                    raise FotoInvalida("Fotografia indisponível.")
                with ImageOps.fit(foto, (192, 192), method=Image.Resampling.LANCZOS) as pequena:
                    pequena.info.clear()
                    with BytesIO() as saida:
                        pequena.save(saida, format="WEBP", quality=70)
                        return saida.getvalue()
    except (OSError, ValueError, SyntaxError, EOFError,
            Image.DecompressionBombWarning, Image.DecompressionBombError) as erro:
        raise FotoInvalida("Fotografia indisponível.") from erro
