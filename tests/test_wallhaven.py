import importlib
import sys
import types
import unittest
from unittest.mock import patch
from urllib.parse import parse_qsl, urlparse


class _Schema:
    def __init__(self, valores=None):
        self.valores = valores or {}
        self.guardados = {}

    def get_string(self, chave):
        return self.valores.get(chave, "")

    def set_string(self, chave, valor):
        self.guardados[chave] = valor


class _Resposta:
    def __init__(self, dados):
        self._dados = dados
        self.chamadas = {}

    def raise_for_status(self):
        return None

    def json(self):
        return self._dados


def _item(id_, favoritos, pureza="sfw"):
    return {
        "id": id_,
        "path": f"https://w.wallhaven.cc/full/{id_}.jpg",
        "resolution": "1920x1080",
        "dimension_x": 1920,
        "dimension_y": 1080,
        "favorites": favoritos,
        "purity": pureza,
        "category": "general",
        "thumbs": {"large": f"https://th.wallhaven.cc/lg/{id_}.jpg"},
    }


class WallhavenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_shared = sys.modules.get("cartridges.shared")
        shared = types.ModuleType("cartridges.shared")
        shared.schema = _Schema()
        sys.modules["cartridges.shared"] = shared
        sys.modules.pop("cartridges.utils.wallhaven", None)
        cls.module = importlib.import_module("cartridges.utils.wallhaven")
        cls.shared = shared

    @classmethod
    def tearDownClass(cls):
        sys.modules.pop("cartridges.utils.wallhaven", None)
        if cls.original_shared is not None:
            sys.modules["cartridges.shared"] = cls.original_shared
        else:
            sys.modules.pop("cartridges.shared", None)

    def setUp(self):
        self.shared.schema = _Schema()

    def test_padrao_e_seguro(self):
        filtros = self.module.Filtros()
        self.assertEqual(filtros.categorias, "100")
        self.assertEqual(filtros.pureza, "100")
        self.assertEqual(filtros.ordenacao, "relevance")
        self.assertEqual(filtros.ordem, "desc")
        self.assertFalse(filtros.restrito)

    def test_flags_invalidas_levantam(self):
        with self.assertRaises(ValueError):
            self.module.Filtros(categorias="000")
        with self.assertRaises(ValueError):
            self.module.Filtros(categorias="1")
        with self.assertRaises(ValueError):
            self.module.Filtros(pureza="000")
        with self.assertRaises(ValueError):
            self.module.Filtros(ordenacao="bogus")
        with self.assertRaises(ValueError):
            self.module.Filtros(ordem="sideways")
        with self.assertRaises(ValueError):
            self.module.Filtros(alcance="2y")
        with self.assertRaises(ValueError):
            self.module.Filtros(cores="zzz")
        with self.assertRaises(ValueError):
            self.module.Filtros(cores="12345")

    def test_parametros_padrao(self):
        saidos = self.module.parametros("hollow knight", 1920, 1080)
        self.assertEqual(
            saidos,
            {
                "q": "hollow knight",
                "categories": "100",
                "purity": "100",
                "sorting": "relevance",
                "order": "desc",
                "atleast": "1920x1080",
                "page": "1",
            },
        )

    def test_parametros_completos_com_chave(self):
        self.shared.schema = _Schema({"wallhaven-key": "abc"})
        filtros = self.module.Filtros(
            categorias="111",
            pureza="111",
            ordenacao="toplist",
            ordem="asc",
            alcance="1w",
            cores="EA4C88",
        )
        saidos = self.module.parametros(
            "jogo",
            1080,
            1920,
            formato="portrait",
            pagina=2,
            filtros=filtros,
            resolucoes="1080x1920",
            proporcoes="9x16",
        )
        self.assertEqual(saidos["categories"], "111")
        self.assertEqual(saidos["purity"], "111")
        self.assertEqual(saidos["sorting"], "toplist")
        self.assertEqual(saidos["order"], "asc")
        self.assertEqual(saidos["topRange"], "1w")
        self.assertEqual(saidos["colors"], "ea4c88")
        self.assertEqual(saidos["ratios"], "portrait,9x16")
        self.assertEqual(saidos["resolutions"], "1080x1920")
        self.assertEqual(saidos["page"], "2")

    def test_toprange_so_no_toplist_e_seed_so_no_random(self):
        self.shared.schema = _Schema({"wallhaven-key": "abc"})
        filtros = self.module.Filtros(ordenacao="favorites")
        saidos = self.module.parametros("jogo", 1920, 1080, filtros=filtros)
        self.assertNotIn("topRange", saidos)
        self.assertNotIn("seed", saidos)

        aleatorios = self.module.Filtros(ordenacao="random")
        com_seed = self.module.parametros(
            "jogo", 1920, 1080, filtros=aleatorios, seed="a1B2c3"
        )
        self.assertEqual(com_seed["seed"], "a1B2c3")
        sem_seed = self.module.parametros("jogo", 1920, 1080, filtros=aleatorios)
        self.assertNotIn("seed", sem_seed)

    def test_restrito_sem_chave_levanta(self):
        for pureza in ("110", "101", "111", "010", "001"):
            with self.subTest(pureza=pureza), self.assertRaises(
                self.module.WallhavenError
            ):
                self.module.parametros(
                    "jogo", 1920, 1080, filtros=self.module.Filtros(pureza=pureza)
                )

    def test_seed_invalida_levanta(self):
        with self.assertRaises(ValueError):
            self.module.parametros("jogo", 1920, 1080, seed="curta")
        with self.assertRaises(ValueError):
            self.module.parametros("jogo", 1920, 1080, seed="com espaço!")

    def test_gerar_seed_e_valida(self):
        for _ in range(20):
            seed = self.module.gerar_seed()
            self.module.parametros(
                "jogo",
                1920,
                1080,
                filtros=self.module.Filtros(ordenacao="random"),
                seed=seed,
            )

    def test_buscar_envia_chave_no_cabecalho(self):
        self.shared.schema = _Schema({"wallhaven-key": "abc"})
        capturado = {}

        def falso_get(url, headers=None, timeout=None):
            capturado["url"] = url
            capturado["headers"] = headers
            return _Resposta({"data": [_item("aa", 1), _item("bb", 9)]})

        with patch.object(self.module, "get_capped", side_effect=falso_get):
            resultados = self.module.buscar("jogo", 1920, 1080)

        self.assertEqual(capturado["headers"], {"X-API-Key": "abc"})
        consulta = dict(parse_qsl(urlparse(capturado["url"]).query))
        self.assertEqual(consulta["q"], "jogo")
        self.assertEqual(consulta["atleast"], "1920x1080")
        self.assertEqual(consulta["purity"], "100")
        self.assertEqual([item["id"] for item in resultados], ["bb", "aa"])
        self.assertEqual(resultados[0]["purity"], "sfw")
        self.assertTrue(resultados[0]["thumb"].endswith(".jpg"))

    def test_buscar_sem_chave_sem_cabecalho(self):
        capturado = {}

        def falso_get(url, headers=None, timeout=None):
            capturado["headers"] = headers
            return _Resposta({"data": []})

        with patch.object(self.module, "get_capped", side_effect=falso_get):
            self.assertEqual(self.module.buscar("jogo", 1920, 1080), [])
        self.assertEqual(capturado["headers"], {})

    def test_ler_filtros_higieniza(self):
        self.shared.schema = _Schema(
            {
                "wallhaven-categorias": "111",
                "wallhaven-pureza": "111",
                "wallhaven-ordenacao": "bogus",
                "wallhaven-ordem": "desc",
                "wallhaven-alcance": "1M",
                "wallhaven-cores": "zzz",
            }
        )
        # Sem chave, NSFW volta a SFW; resto inválido volta ao padrão seguro.
        filtros = self.module.ler_filtros()
        self.assertEqual(filtros, self.module.FILTROS_AUTOMATICOS)

        self.shared.schema = _Schema(
            {
                "wallhaven-key": "abc",
                "wallhaven-categorias": "111",
                "wallhaven-pureza": "101",
                "wallhaven-ordenacao": "toplist",
                "wallhaven-ordem": "asc",
                "wallhaven-alcance": "1w",
                "wallhaven-cores": "#EA4C88",
            }
        )
        filtros = self.module.ler_filtros()
        self.assertEqual(filtros.categorias, "111")
        self.assertEqual(filtros.pureza, "101")
        self.assertEqual(filtros.ordenacao, "toplist")
        self.assertEqual(filtros.ordem, "asc")
        self.assertEqual(filtros.alcance, "1w")
        self.assertEqual(filtros.cores, "EA4C88")

    def test_salvar_filtros_persiste(self):
        self.shared.schema = _Schema()
        filtros = self.module.Filtros(categorias="110", ordenacao="favorites")
        self.module.salvar_filtros(filtros)
        self.assertEqual(
            self.shared.schema.guardados,
            {
                "wallhaven-categorias": "110",
                "wallhaven-pureza": "100",
                "wallhaven-ordenacao": "favorites",
                "wallhaven-ordem": "desc",
                "wallhaven-alcance": "1M",
                "wallhaven-cores": "",
            },
        )

    def test_melhor_para_seguro_e_nunca_levanta(self):
        vistos = []

        def falso_buscar(consulta, largura, altura, formato=None, **kwargs):
            vistos.append(kwargs.get("filtros"))
            if consulta == "boom":
                raise self.module.WallhavenError("rede fora")
            return []

        with patch.object(self.module, "buscar", side_effect=falso_buscar):
            self.assertIsNone(self.module.melhor_para("boom", 1920, 1080, "landscape"))
            self.assertIsNone(
                self.module.melhor_para("Jogo Sem Nada", 1920, 1080, "landscape")
            )
        for filtros in vistos:
            self.assertEqual(filtros, self.module.FILTROS_AUTOMATICOS)


if __name__ == "__main__":
    unittest.main()
