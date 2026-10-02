# session_history.py
#
# Copyright 2026 joaomgabaldi
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""A tela do histórico de sessões de um jogo.

Montada em Python em vez de num .blp porque não há layout a descrever: é um
cabeçalho e uma lista de linhas iguais, cujo número só se sabe ao abrir, e ao
lado um gráfico desenhado à mão. Um template teria de declarar a lista vazia e
ser preenchido daqui de qualquer jeito, e custaria mais um arquivo e mais uma
entrada no gresource.
"""

import math
from datetime import date, datetime, timedelta
from typing import Any, Optional

from gi.repository import Adw, Gtk, PangoCairo

from cartridges.game import Game
from cartridges.utils import session_log
from cartridges.utils.create_dialog import create_dialog
from cartridges.utils.format_playtime import format_playtime
from cartridges.utils.game_logo import cached_logo_path, load_logo
from cartridges.utils.relative_date import MONTHS


def format_session_date(timestamp: int) -> str:
    """"12 de agosto de 2026" — a data por extenso, em pt-BR.

    Datas absolutas, e não "Hoje"/"Ontem" como no resto do aplicativo: aqui a
    lista existe para ser lida de cima a baixo, e uma coluna que mistura
    "Terça-feira" com "3 de maio" não deixa comparar duas linhas de relance.
    """
    date = datetime.fromtimestamp(timestamp)
    return f"{date.day} de {MONTHS[date.month - 1].lower()} de {date.year}"


# Nome do período no seletor → quantos dias ele cobre; None é "desde a
# primeira sessão".
PERIODS = {"week": 7, "month": 30, "all": None}


def period_range(
    sessions: list[dict[str, Any]], period: str, today: date
) -> tuple[date, date]:
    """O primeiro e o último dia que o gráfico mostra para ``period``.

    Termina sempre hoje, mesmo sem sessão hoje: um gráfico que acaba no último
    dia jogado esconde justamente os dias parados, que são metade da resposta.
    O início nunca é anterior à primeira sessão já registrada — um histórico
    que só começou há 2 dias não ganha 28 dias vazios só porque o período
    escolhido é "mês".
    """
    first = min(
        (max(0, entry["end"] - entry["seconds"]) for entry in sessions),
        default=None,
    )
    # Uma sessão "no futuro" (relógio adiantado, arquivo editado) não pode
    # inverter a faixa.
    first_date = None
    if first is not None:
        first_date = min(datetime.fromtimestamp(first).date(), today)

    days = PERIODS[period]
    if days is None:
        return first_date or today, today

    start = today - timedelta(days=days - 1)
    return (max(start, first_date) if first_date else start), today


def nice_step(max_hours: float) -> float:
    """O intervalo das linhas de grade: o menor que deixa no máximo 4 delas."""
    for step in (0.25, 0.5, 1, 2, 3, 4, 6, 12, 24):
        if max_hours <= step * 4:
            return step
    return 24.0


def format_hours(hours: float) -> str:
    """"1,5 h" — a vírgula decimal do resto da interface."""
    return f"{hours:g} h".replace(".", ",")


PAGINA = 100


def resumo_dos_ultimos_dias(sessions: list[dict[str, Any]], today: date) -> tuple[int, int]:
    """Segundos jogados nos últimos 7 e 30 dias de calendário, hoje incluído.

    A mesma conta do gráfico (``session_log.daily_seconds``), para que o resumo
    e o período "Semana"/"Mês" nunca discordem.
    """

    def soma(dias: int) -> int:
        inicio = today - timedelta(days=dias - 1)
        return sum(s for _d, s in session_log.daily_seconds(sessions, inicio, today))

    return soma(7), soma(30)


def tempo_ou_nenhuma(seconds: int) -> str:
    """O tempo formatado, ou "nenhuma sessão" quando nada foi jogado."""
    return format_playtime(seconds) if seconds else _("nenhuma sessão")


class PlaytimeChart(Gtk.DrawingArea):
    """Horas jogadas por dia, em linha, desenhadas com cairo.

    A cor da linha vem da classe ``accent`` do próprio widget (a cor de destaque
    roxa que o style.css define), e a do texto e da grade vem do pai: um widget
    só tem uma ``color``, e ler a de destaque pelo ``StyleManager`` daria a do
    sistema, não a do app.
    """

    MARGIN_LEFT = 44
    MARGIN_BOTTOM = 24
    MARGIN_TOP = 8
    MARGIN_RIGHT = 12

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.days: list[tuple[date, int]] = []
        self.add_css_class("accent")
        self.add_css_class("caption")
        self.set_draw_func(self.draw)
        self.set_has_tooltip(True)
        self.connect("query-tooltip", self.on_query_tooltip)

    def set_days(self, days: list[tuple[date, int]]) -> None:
        self.days = days
        self.queue_draw()

    def _x(self, index: int, width: int) -> float:
        plot = width - self.MARGIN_LEFT - self.MARGIN_RIGHT
        if len(self.days) == 1:
            return self.MARGIN_LEFT + plot / 2
        return self.MARGIN_LEFT + plot * index / (len(self.days) - 1)

    def _by_month(self) -> bool:
        # Mais de dois meses na tela e "12/09" vira ruído que não diz o ano;
        # aí o eixo é rotulado por mês.
        return (self.days[-1][0] - self.days[0][0]).days > 60

    def _label_for(self, day: date) -> str:
        if self._by_month():
            return f"{MONTHS[day.month - 1][:3].lower()} {day.year % 100:02d}"
        return f"{day.day:02d}/{day.month:02d}"

    def _text(
        self, cr: Any, text: str, x: float, y: float, align: float, area: int
    ) -> None:
        """Escreve ``text`` com o centro vertical em ``y``. ``align`` é onde
        ``x`` cai no texto: 0 = começo, 0.5 = meio, 1 = fim. ``area`` é a
        largura que o ``draw`` recebeu, e não a do widget: as duas só coincidem
        quando quem chama é o GTK."""
        layout = self.create_pango_layout(text)
        width, height = layout.get_pixel_size()
        # Preso à área do widget: um "set 26" centrado num dia 1º perto da
        # borda sairia cortado.
        left = min(max(0, x - width * align), area - width)
        cr.move_to(left, y - height / 2)
        PangoCairo.show_layout(cr, layout)

    def labeled_indices(self) -> list[int]:
        """Os dias que ganham rótulo no eixo X, no máximo uns 6.

        Por mês, só os dias 1º: um rótulo "jul 26" posto num dia qualquer
        aparecia duas vezes seguidas quando o passo caía dentro do mesmo mês.
        Por dia, sempre o primeiro e o último, e nenhum colado no último a
        ponto de encostar.
        """
        if self._by_month():
            starts = [i for i, (day, _s) in enumerate(self.days) if day.day == 1]
            return starts[:: max(1, math.ceil(len(starts) / 6))]

        count = len(self.days)
        every = max(1, math.ceil(count / 6))
        labeled = list(range(0, count, every))
        if labeled[-1] != count - 1:
            if count - 1 - labeled[-1] < every / 2 and len(labeled) > 1:
                labeled.pop()
            labeled.append(count - 1)
        return labeled

    def draw(self, _area: Any, cr: Any, width: int, height: int) -> None:
        if not self.days:
            return

        parent = self.get_parent()
        fg = (parent or self).get_color()
        accent = self.get_color()

        hours = [seconds / 3600 for _day, seconds in self.days]
        step = nice_step(max(hours))
        top = step * max(1, math.ceil(max(hours) / step))

        bottom_y = height - self.MARGIN_BOTTOM
        plot_h = bottom_y - self.MARGIN_TOP

        def y_of(value: float) -> float:
            return bottom_y - plot_h * value / top

        # Grade e eixo Y
        cr.set_line_width(1)
        for i in range(round(top / step) + 1):
            value = step * i
            y = round(y_of(value)) + 0.5
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.12)
            cr.move_to(self.MARGIN_LEFT, y)
            cr.line_to(width - self.MARGIN_RIGHT, y)
            cr.stroke()
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.6)
            self._text(cr, format_hours(value), self.MARGIN_LEFT - 6, y, 1, width)

        # Eixo X; as pontas alinham para dentro para não sair da área.
        count = len(self.days)
        for index in self.labeled_indices():
            if count == 1:
                align = 0.5
            else:
                align = 0.0 if index == 0 else 1.0 if index == count - 1 else 0.5
            self._text(
                cr,
                self._label_for(self.days[index][0]),
                self._x(index, width),
                bottom_y + self.MARGIN_BOTTOM / 2 + 2,
                align,
                width,
            )

        points = [(self._x(i, width), y_of(h)) for i, h in enumerate(hours)]

        if count > 1:
            # Área sob a linha, bem clara, para o volume ser lido de relance.
            cr.move_to(points[0][0], bottom_y)
            for x, y in points:
                cr.line_to(x, y)
            cr.line_to(points[-1][0], bottom_y)
            cr.close_path()
            cr.set_source_rgba(accent.red, accent.green, accent.blue, 0.15)
            cr.fill()

            cr.set_source_rgba(accent.red, accent.green, accent.blue, 1)
            cr.set_line_width(2)
            cr.set_line_join(1)  # cairo.LINE_JOIN_ROUND
            cr.move_to(*points[0])
            for x, y in points[1:]:
                cr.line_to(x, y)
            cr.stroke()

        # Pontos só enquanto cabem: num período de anos eles viram uma faixa.
        plot_w = width - self.MARGIN_LEFT - self.MARGIN_RIGHT
        if count == 1 or plot_w / count >= 8:
            cr.set_source_rgba(accent.red, accent.green, accent.blue, 1)
            for x, y in points:
                cr.arc(x, y, 3, 0, 2 * math.pi)
                cr.fill()

    def on_query_tooltip(
        self, _widget: Any, x: int, _y: int, _keyboard: bool, tooltip: Gtk.Tooltip
    ) -> bool:
        """O dia sob o mouse e quanto se jogou nele: o eixo Y só dá a ordem de
        grandeza."""
        if not self.days:
            return False
        # O inverso de `_x`, que é linear: sem varrer todos os dias a cada
        # movimento do mouse, o que em anos de histórico são milhares.
        plot = self.get_width() - self.MARGIN_LEFT - self.MARGIN_RIGHT
        last = len(self.days) - 1
        index = 0
        if last > 0 and plot > 0:
            index = min(max(round((x - self.MARGIN_LEFT) / plot * last), 0), last)
        day, seconds = self.days[index]
        stamp = int(datetime.combine(day, datetime.min.time()).timestamp())
        tooltip.set_text(f"{format_session_date(stamp)}: {tempo_ou_nenhuma(seconds)}")
        return True


# A largura da caixa e as margens laterais do conteúdo.
DIALOG_WIDTH = 1040
MARGIN = 24

# O logo daqui é a manchete da caixa inteira, não o título de uma coluna: bem
# maior que o da tela de detalhes (72 de altura), e a largura útil é a caixa
# toda.
HEADER_LOGO_MAX_HEIGHT = 120
HEADER_LOGO_MAX_WIDTH = DIALOG_WIDTH - 2 * MARGIN

# Umas 8 linhas de sessão. Passou disso, a tabela rola em vez de esticar a
# caixa de diálogo para fora da janela.
TABLE_MAX_HEIGHT = 440


class SessionHistoryDialog(Adw.Dialog):
    """As sessões de um jogo, com a opção de apagar uma que contou errado.

    De cima para baixo: o logo (ou o nome) e o resumo dos últimos dias, os dois
    centralizados na largura toda; embaixo, lado a lado, a tabela de sessões e
    o painel do gráfico. O painel tem sempre a altura da faixa, e a faixa segue
    a da tabela — por isso a caixa não tem altura fixa, só largura.
    """

    def __init__(self, game: Game, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        self.game = game
        self.set_title(_("Histórico de sessões"))
        self.set_content_width(DIALOG_WIDTH)

        self._rows: list[Gtk.Widget] = []

        self.summary = Gtk.Label(halign=Gtk.Align.CENTER, wrap=True)
        self.summary.add_css_class("dim-label")

        self.columns = Gtk.Box(spacing=MARGIN, margin_top=12)
        self.columns.append(self._build_table())
        self.columns.append(self._build_chart_panel())

        body = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_start=MARGIN,
            margin_end=MARGIN,
            margin_bottom=MARGIN,
        )
        body.append(self._build_header())
        body.append(self.summary)
        body.append(self.columns)

        toolbar = Adw.ToolbarView()
        # O logo abre a tela logo abaixo da barra, e o título ali ficava colado
        # nele. Só a barra o esconde: o diálogo mantém o nome, que é o da
        # janela e o que o leitor de tela anuncia.
        toolbar.add_top_bar(Adw.HeaderBar(show_title=False))
        toolbar.set_content(body)
        self.set_child(toolbar)

        self._mostradas = PAGINA
        self.rebuild()

    def _build_header(self) -> Gtk.Widget:
        """O logo do jogo, ou o nome escrito quando não há logo.

        Só o que já está em disco: a busca no SteamGridDB é da tela de detalhes,
        de onde esta aqui é aberta. O logo *é* o título quando existe, então os
        dois nunca aparecem juntos.

        Só a largura é imposta, pelo clamp: a altura sai de height-for-width, e
        a proporção do logo é preservada por construção. Um ``set_size_request``
        no Picture não serve — ele é um piso, não um teto, e o widget receberia
        a largura inteira da caixa, crescendo junto na altura.
        """
        self.logo: Optional[Gtk.Picture] = None
        self.name_label: Optional[Gtk.Label] = None

        path = cached_logo_path(self.game)
        loaded = (
            load_logo(path, HEADER_LOGO_MAX_WIDTH, HEADER_LOGO_MAX_HEIGHT)
            if path
            else None
        )
        if not loaded:
            self.name_label = Gtk.Label(
                label=self.game.name,
                halign=Gtk.Align.CENTER,
                wrap=True,
                justify=Gtk.Justification.CENTER,
            )
            self.name_label.add_css_class("title-1")
            return self.name_label

        texture, width = loaded
        self.logo = Gtk.Picture(
            paintable=texture,
            content_fit=Gtk.ContentFit.CONTAIN,
            can_shrink=True,
        )
        return Adw.Clamp(
            unit=Adw.LengthUnit.PX,
            # Os dois no mesmo valor para o clamp parar de interpolar entre uma
            # largura "apertada" e a cheia, e simplesmente alocar a pedida.
            maximum_size=width,
            tightening_threshold=width,
            halign=Gtk.Align.CENTER,
            child=self.logo,
        )

    def _build_table(self) -> Gtk.Widget:
        """A lista de sessões, rolando a partir de ``TABLE_MAX_HEIGHT``.

        Uma ListBox simples, e não um Adw.PreferencesPage: a página tem rolagem
        e margens próprias e ocupa toda a altura que recebe, então a tabela
        nunca terminava junto com o painel do gráfico ao lado. Aqui a rolagem
        propaga a altura natural da lista, e é ela que dá a altura da faixa.
        """
        # START: com poucas linhas, a faixa fica na altura mínima do gráfico, e
        # a lista não pode esticar junto — sobraria um cartão vazio embaixo.
        self.table = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE, valign=Gtk.Align.START
        )
        self.table.add_css_class("boxed-list")

        self.mais_button = Gtk.Button(label=_("Mostrar mais sessões"), halign=Gtk.Align.CENTER)
        self.mais_button.add_css_class("flat")
        self.mais_button.connect("clicked", lambda *_: self.mostrar_mais())
        caixa = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        caixa.append(self.table)
        caixa.append(self.mais_button)

        return Gtk.ScrolledWindow(
            child=caixa,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_natural_height=True,
            max_content_height=TABLE_MAX_HEIGHT,
            width_request=440,
        )

    def _build_chart_panel(self) -> Gtk.Widget:
        """O gráfico de horas por dia, à direita da lista.

        O seletor de período fica em cima, onde o olho chega antes do gráfico:
        é ele que diz o que o gráfico está mostrando. O painel preenche a
        altura da faixa, que é a da tabela; o piso é o ``height_request`` do
        desenho, para uma tabela de uma linha não achatar o gráfico.
        """
        self.period = Adw.ToggleGroup(halign=Gtk.Align.CENTER)
        for name, label in (
            ("week", _("Semana")),
            ("month", _("Mês")),
            ("all", _("Todo o período")),
        ):
            self.period.add(Adw.Toggle(name=name, label=label))
        self.period.set_active_name("month")
        self.period.connect("notify::active-name", lambda *_: self.update_chart())

        self.chart_total = Gtk.Label(halign=Gtk.Align.CENTER)
        self.chart_total.add_css_class("dim-label")

        self.chart = PlaytimeChart(
            hexpand=True, vexpand=True, width_request=320, height_request=240
        )

        self.chart_panel = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=12, hexpand=True
        )
        self.chart_panel.append(self.period)
        self.chart_panel.append(self.chart_total)
        self.chart_panel.append(self.chart)
        return self.chart_panel

    def update_chart(self) -> None:
        """Recalcula os dias do período escolhido a partir das sessões lidas."""
        first, last = period_range(
            self._sessions, self.period.get_active_name(), date.today()
        )
        days = session_log.daily_seconds(self._sessions, first, last)
        self.chart.set_days(days)
        # A variável é o tempo jogado no período escolhido
        self.chart_total.set_label(
            _("{} no período").format(
                tempo_ou_nenhuma(sum(seconds for _day, seconds in days))
            )
        )

    def rebuild(self) -> None:
        """Relê o arquivo e redesenha a lista inteira.

        Redesenhar tudo em vez de tirar uma linha da tela: a remoção mexe no
        total dos resumos do cabeçalho também, e recarregar é a única forma de
        garantir que a tela mostra o que está gravado, e não o que ela acha que
        gravou.
        """
        for row in self._rows:
            self.table.remove(row)
        self._rows.clear()

        sessions = session_log.load(self.game.game_id)
        self._sessions = sessions

        # Sem sessão não há o que listar nem desenhar: a frase do resumo
        # explica o vazio, e uma tabela vazia ao lado de um gráfico todo em
        # zero só a repetiria.
        self.columns.set_visible(bool(sessions))
        if not sessions:
            self.summary.set_label(_("Nenhuma sessão registrada."))
            return

        sete, trinta = resumo_dos_ultimos_dias(sessions, date.today())
        self.summary.set_label(
            # As variáveis são os tempos jogados nos últimos 7 e 30 dias
            _("Últimos 7 dias: {} · últimos 30 dias: {}").format(
                tempo_ou_nenhuma(sete), tempo_ou_nenhuma(trinta)
            )
        )
        self.update_chart()

        for session in sessions[: self._mostradas]:
            self.table.append(row := self._build_row(session))
            self._rows.append(row)
        self.mais_button.set_visible(len(sessions) > self._mostradas)

    def mostrar_mais(self) -> None:
        """Acrescenta a próxima página de sessões ao fim da tabela."""
        inicio = self._mostradas
        self._mostradas += PAGINA
        for session in self._sessions[inicio : self._mostradas]:
            self.table.append(row := self._build_row(session))
            self._rows.append(row)
        self.mais_button.set_visible(len(self._sessions) > self._mostradas)

    def _build_row(self, session: dict[str, Any]) -> Adw.ActionRow:
        ended = datetime.fromtimestamp(session["end"])
        row = Adw.ActionRow(
            title=format_session_date(session["end"]),
            # As variáveis são a hora em que a sessão terminou e sua duração
            subtitle=_("Terminou às {} · {}").format(
                ended.strftime("%H:%M"), format_playtime(session["seconds"])
            ),
        )

        button = Gtk.Button(
            icon_name="user-trash-symbolic",
            valign=Gtk.Align.CENTER,
            tooltip_text=_("Excluir esta sessão"),
        )
        button.add_css_class("flat")
        button.connect("clicked", self.confirm_delete, session)
        row.add_suffix(button)
        return row

    def confirm_delete(self, _widget: Any, session: dict[str, Any]) -> None:
        """Pergunta antes de apagar: a linha não volta, e o total vai junto."""
        create_dialog(
            self,
            _("Excluir esta sessão?"),
            # As variáveis são a duração da sessão e a data em que ela terminou
            _(
                "Tem certeza que deseja excluir a sessão de {}, em {}? O "
                "tempo será descontado do total do jogo. Esta ação é "
                "irreversível."
            ).format(
                format_playtime(session["seconds"]),
                format_session_date(session["end"]),
            ),
            "delete",
            _("Excluir"),
            destructive=True,
        ).connect("response", self.on_delete_response, session)

    def on_delete_response(
        self, _dialog: Any, response: str, session: dict[str, Any]
    ) -> None:
        if response != "delete":
            return

        if not session_log.delete(
            self.game.game_id, session["end"], session["seconds"]
        ):
            # A linha sumiu entre abrir a tela e confirmar (outra janela, o
            # arquivo editado à mão). Nada a descontar: o total continua
            # correspondendo ao que sobrou no histórico.
            self.rebuild()
            return

        # Nunca abaixo de zero: um total menor que a sessão só acontece com um
        # registro editado à mão, e o certo aí é zerar, não guardar um tempo
        # de jogo negativo que a ordenação levaria a sério.
        self.game.playtime = max(0, self.game.playtime - session["seconds"])
        self.game.save()
        self.game.update()
        self.rebuild()
