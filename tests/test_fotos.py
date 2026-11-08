import random
import struct
import unittest
import zlib
from io import BytesIO
from unittest.mock import patch

from PIL import Image
from werkzeug.datastructures import FileStorage

from app.fotos import FotoInvalida, processar_foto


def imagem(formato="PNG", tamanho=(80, 40), modo="RGB", **opcoes):
    with Image.new(modo, tamanho, (30, 120, 80, 90) if modo == "RGBA" else "red") as foto:
        saida = BytesIO()
        foto.save(saida, format=formato, **opcoes)
        return saida.getvalue()


def upload(conteudo, nome="foto.jpg", mime="image/jpeg"):
    return FileStorage(stream=BytesIO(conteudo), filename=nome, content_type=mime)


class FotosTests(unittest.TestCase):
    def conferir_webp(self, resultado, tamanho):
        self.assertEqual(resultado.content_type, "image/webp")
        self.assertIsInstance(resultado.conteudo, bytes)
        self.assertLessEqual(len(resultado.conteudo), 512_000)
        with Image.open(BytesIO(resultado.conteudo)) as foto:
            foto.load()
            self.assertEqual(foto.format, "WEBP")
            self.assertEqual(foto.size, tamanho)
            self.assertEqual((resultado.largura, resultado.altura), tamanho)
            self.assertFalse(foto.getexif())

    def test_formatos_reais_independem_de_nome_e_mime(self):
        for formato in ("JPEG", "PNG", "WEBP"):
            with self.subTest(formato=formato):
                self.conferir_webp(processar_foto(upload(imagem(formato), "foto.exe",
                                                       "application/octet-stream")), (80, 40))

    def test_transparencia_rgba_e_paleta(self):
        for modo in ("RGBA", "P"):
            with self.subTest(modo=modo):
                if modo == "P":
                    with Image.new("P", (80, 40), 0) as foto:
                        saida = BytesIO()
                        foto.save(saida, format="PNG", transparency=0)
                        dados = saida.getvalue()
                else:
                    dados = imagem(modo=modo)
                resultado = processar_foto(upload(dados))
                with Image.open(BytesIO(resultado.conteudo)) as foto:
                    self.assertEqual(foto.mode, "RGBA")
                    self.assertEqual(foto.getpixel((0, 0))[3], 0 if modo == "P" else 90)

    def test_corrige_orientacao_exif_e_remove_metadados(self):
        exif = Image.Exif()
        exif[274] = 6
        exif[315] = "Autor privado"
        resultado = processar_foto(upload(imagem("JPEG", exif=exif)))
        self.conferir_webp(resultado, (40, 80))
        self.assertNotIn(b"Autor privado", resultado.conteudo)

    def test_converte_escala_de_cinza_e_jpeg_cmyk(self):
        for modo, formato in (("L", "PNG"), ("CMYK", "JPEG")):
            with self.subTest(modo=modo):
                self.conferir_webp(processar_foto(upload(imagem(formato, modo=modo))), (80, 40))

    def test_limite_final_aceita_tamanho_exato(self):
        dados = imagem()
        tamanho = len(processar_foto(upload(dados)).conteudo)
        with patch("app.fotos.MAX_WEBP_BYTES", tamanho):
            resultado = processar_foto(upload(dados))
        self.assertEqual(len(resultado.conteudo), tamanho)

    def test_redimensiona_proporcionalmente_sem_ampliar(self):
        for entrada, esperado in (((2400, 1200), (1600, 800)),
                                  ((1200, 2400), (800, 1600)),
                                  ((80, 40), (80, 40))):
            with self.subTest(entrada=entrada):
                self.conferir_webp(processar_foto(upload(imagem(tamanho=entrada))), esperado)

    def test_vazios_invalidos_corrompidos_e_formatos_proibidos(self):
        png = imagem()
        jpeg = imagem("JPEG")
        crc_invalido = png[:29] + b"\0\0\0\0" + png[33:]
        for dados in (b"", b"nao e imagem", png[:45], crc_invalido, jpeg[:len(jpeg)//2],
                      imagem("GIF"), imagem("BMP"), imagem("TIFF")):
            with self.subTest(inicio=dados[:12]):
                with self.assertRaises(FotoInvalida) as erro:
                    processar_foto(upload(dados))
                self.assertTrue(str(erro.exception))

    def test_limite_original_inclusivo_e_leitura_limitada(self):
        dados = imagem("JPEG")
        limite = 3 * 1024 * 1024
        self.conferir_webp(processar_foto(upload(dados + b"\0" * (limite-len(dados)))), (80, 40))
        arquivo = upload(dados + b"\0" * (limite+100-len(dados)))
        with self.assertRaisesRegex(FotoInvalida, "3 MiB"):
            processar_foto(arquivo)
        self.assertLessEqual(arquivo.stream.tell(), limite+1)

    def test_dimensoes_excessivas_no_cabecalho(self):
        # Altera somente IHDR/CRC: rejeição deve acontecer antes de decodificar pixels.
        original = imagem()
        for largura, altura in ((10001, 1), (5000, 4001), (100000, 100000)):
            ihdr = struct.pack(">II", largura, altura) + original[24:29]
            dados = original[:16] + ihdr + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr)) + original[33:]
            with self.subTest(tamanho=(largura, altura)):
                with self.assertRaisesRegex(FotoInvalida, "dimensões"):
                    processar_foto(upload(dados))

    def test_rejeita_animacao(self):
        with Image.new("RGB", (20, 20), "red") as primeira, Image.new("RGB", (20, 20), "blue") as segunda:
            saida = BytesIO()
            primeira.save(saida, format="WEBP", save_all=True, append_images=[segunda])
        with self.assertRaisesRegex(FotoInvalida, "animad"):
            processar_foto(upload(saida.getvalue()))

    def test_foto_complexa_respeita_limite_final(self):
        dados = random.Random(42).randbytes(1600 * 1600 * 3)
        with Image.frombytes("RGB", (1600, 1600), dados) as foto:
            saida = BytesIO()
            foto.save(saida, format="JPEG", quality=90)
        self.assertLess(len(saida.getvalue()), 3 * 1024 * 1024)
        resultado = processar_foto(upload(saida.getvalue()))
        self.conferir_webp(resultado, (resultado.largura, resultado.altura))
        self.assertEqual(resultado.largura, resultado.altura)
        self.assertGreaterEqual(resultado.largura, 320)

    def test_reduz_dimensoes_quando_qualidade_nao_basta(self):
        # Limite menor exercita o fallback com o encoder real, sem imagens enormes.
        dados = random.Random(7).randbytes(800 * 400 * 4)
        with Image.frombytes("RGBA", (800, 400), dados) as foto:
            saida = BytesIO()
            foto.save(saida, format="WEBP", lossless=True)
        with patch("app.fotos.MAX_WEBP_BYTES", 100_000):
            resultado = processar_foto(upload(saida.getvalue()))
        self.assertLessEqual(len(resultado.conteudo), 100_000)
        self.assertLess(resultado.largura, 800)
        self.assertAlmostEqual(resultado.largura / resultado.altura, 2, delta=0.01)

    def test_erro_controlado_se_limite_exigir_degradacao_excessiva(self):
        with patch("app.fotos.MAX_WEBP_BYTES", 1):
            with self.assertRaisesRegex(FotoInvalida, "reduzir"):
                processar_foto(upload(imagem()))
