from PyQt6 import QtCore, QtWidgets as W
from mainnet_electrum import SERVERS, server_endpoint
from concurrent.futures import ThreadPoolExecutor
from wallet_keys import KeySet, own_account, public
from pathlib import Path
import json
from os.path import isreserved
import platform
import sys

import electrum
from PyQt6.QtCore import Qt, PYQT_VERSION_STR, qVersion
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                             QFormLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
                             QRadioButton, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
                             QWizard, QWizardPage)
from desktop_wallet import Wallet, account_from_words
from pep_wallet import create_phrase, COIN


from pep_version import VERSION
PEP_LOGO = Path(__file__).resolve().parent / "assets" / "pepecoin.png"
ICONS = Path(electrum.__file__).parent / "gui" / "icons"
TITLE_BAR = (Qt.WindowType.CustomizeWindowHint | Qt.WindowType.WindowTitleHint | Qt.WindowType.WindowSystemMenuHint |
             Qt.WindowType.WindowMinMaxButtonsHint | Qt.WindowType.WindowCloseButtonHint)
DIALOG_FLAGS = Qt.WindowType.Dialog | TITLE_BAR


def icon(name):
    return QIcon(str(ICONS / name))


def notice_language(languages, translations):
    for tag in languages:
        locale = QtCore.QLocale(tag)
        region = locale.name()
        language = region.split("_")[0]
        if language == "zh":
            return "zh_Hant" if locale.script() == QtCore.QLocale.Script.TraditionalHanScript else "zh_Hans"
        if language == "sr":
            return "sr_Latn" if locale.script() == QtCore.QLocale.Script.LatinScript else "sr_Cyrl"
        for key in (region, language):
            if key in translations:
                return key
        if language == "pt":
            return "pt_PT"
    return "en"


def show_startup_notice(settings, parent):
    setting = "startup/hideVersionReminder"
    if settings.value(setting, False, bool):
        return
    catalog = json.loads(PEP_LOGO.with_name("startup-notice.json").read_text(encoding="utf-8"))
    languages = QtCore.QLocale.system().uiLanguages()
    message, checkbox = catalog[notice_language(languages, catalog)]
    box = QMessageBox(parent)
    box.setWindowTitle("Electrum-PEP")
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setLayoutDirection(QtCore.QLocale(languages[0]).textDirection() if languages else Qt.LayoutDirection.LeftToRight)
    box.setText(message)
    box.setIconPixmap(QIcon(str(PEP_LOGO)).pixmap(48,48))
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    hide = QCheckBox(checkbox)
    box.setCheckBox(hide)
    box.exec()
    if hide.isChecked():
        settings.setValue(setting, True)
        settings.sync()


def password_input():
    box = QLineEdit()
    box.setEchoMode(QLineEdit.EchoMode.Password)
    return box


def info_table(parent, title, fields):
    dlg = QDialog(parent, DIALOG_FLAGS)
    dlg.setWindowTitle(title)
    form = QFormLayout(dlg)
    if title.startswith("О программе") or title == "Информация о кошельке":
        logo = QLabel()
        logo.setPixmap(QPixmap(str(PEP_LOGO)).scaledToHeight(64, Qt.TransformationMode.SmoothTransformation))
        form.addRow(logo)
    for key, value in fields:
        label = QLabel(str(value))
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow(key, label)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    buttons.rejected.connect(dlg.reject)
    form.addRow(buttons)
    dlg.setMinimumWidth(465)
    dlg.exec()


def about(parent):
    info_table(parent, "О программе Electrum-PEP", [
        ("Приложение", f"Electrum-PEP {VERSION} · {parent.wallet.network}"), ("Компоненты Electrum", electrum.__version__),
        ("Qt", qVersion()), ("PyQt", PYQT_VERSION_STR), ("Python", platform.python_version()),
        ("Система", f"{platform.system()} {platform.release()} · {platform.machine()}"),
        ("Исполняемый файл", sys.executable),
    ])


def preferences(parent, settings):
    dlg = QDialog(parent, DIALOG_FLAGS)
    dlg.setWindowTitle("Настройки")
    layout = QVBoxLayout(dlg)
    tabs = QTabWidget()
    layout.addWidget(tabs)
    appearance = QWidget()
    form = QFormLayout(appearance)
    zeros = QSpinBox()
    zeros.setRange(0, 8)
    zeros.setValue(settings.value("zeros", 0, int))
    form.addRow("Минимум знаков после запятой", zeros)
    alternating = QCheckBox("Чередовать цвет строк в таблицах")
    alternating.setChecked(settings.value("alternating", True, bool))
    form.addRow(alternating)
    tabs.addTab(appearance, "Оформление")
    network = QWidget()
    form = QFormLayout(network)
    interval = QSpinBox()
    interval.setRange(15, 600)
    interval.setSuffix(" сек.")
    interval.setValue(settings.value("interval", 60, int))
    form.addRow("Обновление баланса и истории", interval)
    rate = QDoubleSpinBox()
    rate.setDecimals(3)
    rate.setRange(.001, 10)
    rate.setSingleStep(.001)
    rate.setValue(settings.value("fee_rate", COIN // 100, int) / COIN)
    form.addRow("Комиссия, PEP/кБ", rate)
    tabs.addTab(network, "Транзакции")
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    layout.addWidget(buttons)
    if not dlg.exec():
        return False
    for key, value in (("zeros", zeros.value()), ("alternating", alternating.isChecked()),
                       ("interval", interval.value()), ("fee_rate", round(rate.value() * COIN))):
        settings.setValue(key, value)
    settings.sync()
    return True


class NetworkDialog(W.QDialog):
    def __init__(self, window):
        super().__init__(window, DIALOG_FLAGS)
        self.window = window
        self.setWindowTitle("Сеть")
        self.setMinimumWidth(480)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        layout = W.QVBoxLayout(self)
        status_box = W.QGroupBox("Pepecoin mainnet")
        form = W.QFormLayout(status_box)
        coin_logo = QLabel()
        coin_logo.setPixmap(QIcon(str(PEP_LOGO)).pixmap(36,36))
        form.addRow(coin_logo)
        self.state, self.peer, self.sync, self.error = (W.QLabel() for _ in range(4))
        for title, widget in (("Состояние", self.state), ("Сервер", self.peer),
                              ("Последняя синхронизация", self.sync), ("Ошибка", self.error)):
            widget.setTextFormat(Qt.TextFormat.PlainText)
            widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            widget.setWordWrap(True)
            form.addRow(title, widget)
        layout.addWidget(status_box)
        controls = W.QGroupBox("Выбор сервера")
        form = W.QFormLayout(controls)
        self.auto = W.QCheckBox("Выбирать сервер автоматически")
        self.auto.setChecked(window.connection.manual is None)
        form.addRow(self.auto)
        self.host = W.QComboBox()
        self.host.setEditable(True)
        self.host.addItems(SERVERS)
        self.port = W.QSpinBox()
        self.port.setRange(1, 65535)
        self.host.setCurrentText(window.settings.value("server/host", SERVERS[0], str))
        self.port.setValue(window.settings.value("server/port", 50002, int))
        self.host.activated.connect(lambda _: self.port.setValue(50002))
        form.addRow("Адрес сервера", self.host)
        form.addRow("Порт TLS", self.port)
        self.auto.toggled.connect(self.host.setDisabled)
        self.auto.toggled.connect(self.port.setDisabled)
        self.host.setDisabled(self.auto.isChecked())
        self.port.setDisabled(self.auto.isChecked())
        layout.addWidget(controls)
        buttons = W.QDialogButtonBox()
        self.apply_button = buttons.addButton("Подключиться", W.QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton("Закрыть", W.QDialogButtonBox.ButtonRole.RejectRole)
        self.apply_button.clicked.connect(self.apply)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.update_status)
        self.timer.start(200)
        self.update_status()

    def update_status(self):
        state, peer, error = self.window.connection.status
        self.state.setText(state)
        self.peer.setText(peer or "—")
        self.sync.setText(self.window.last_sync or "—")
        self.error.setText((error or self.window.last_error or "—")[:600])
        self.apply_button.setEnabled(not self.window.busy)

    def apply(self):
        if self.window.busy:
            return
        try:
            manual = None if self.auto.isChecked() else server_endpoint(self.host.currentText(), self.port.value())
        except ValueError as exc:
            W.QMessageBox.warning(self, "Адрес сервера", str(exc))
            return
        self.window.set_server(manual)
        self.update_status()


class WalletWizard(QWizard):
    def __init__(self, directory, selected=None, parent=None, *, network="mainnet"):
        self.network = network
        super().__init__(parent, DIALOG_FLAGS)
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.wallet = None
        self.setWindowTitle(f"Electrum-PEP — Мастер кошельков [{network}]")
        self.setWizardStyle(QWizard.WizardStyle.ClassicStyle)
        self.setPixmap(QWizard.WizardPixmap.LogoPixmap, QPixmap(str(PEP_LOGO)).scaled(56, 56, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        for which, text in ((self.WizardButton.NextButton, "Далее >"), (self.WizardButton.BackButton, "< Назад"),
                            (self.WizardButton.FinishButton, "Открыть кошелёк"), (self.WizardButton.CancelButton, "Отмена")):
            self.setButtonText(which, text)
        self.resize(590, 390)
        first = QWizardPage()
        first.setTitle("Файл кошелька")
        first.setSubTitle("Выберите сохранённый кошелёк или введите имя нового.")
        form = QFormLayout(first)
        self.name = QComboBox()
        self.name.setEditable(True)
        files = (p for p in self.directory.iterdir() if p.is_file() and not p.name.endswith(".pep-lock")
                 and not (p.suffix[1:].isdigit() and p.stem.endswith(".tmp")))
        filenames = sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)
        self.name.addItems([p.name for p in filenames])
        self.name.setCurrentText(selected.name if selected else (filenames[0].name if filenames else "default_wallet"))
        form.addRow("Сеть", QLabel("Pepecoin " + network))
        form.addRow("Кошелёк", self.name)
        form.addRow("Папка", QLabel(str(directory)))
        self.addPage(first)
        types = QWizardPage()
        types.setTitle("Тип кошелька")
        types.setSubTitle("Какой кошелёк Pepecoin вы хотите создать?")
        options = QVBoxLayout(types)
        self.standard = QRadioButton("Стандартный кошелёк")
        self.multi = QRadioButton("Кошелёк с несколькими подписями")
        self.imported = QRadioButton("Импорт адресов или приватных ключей Pepecoin")
        self.standard.setChecked(True)
        for radio in (self.standard, self.multi, self.imported):
            options.addWidget(radio)
        counts = W.QWidget()
        line = W.QHBoxLayout(counts)
        self.required, self.total = QSpinBox(), QSpinBox()
        self.total.setRange(2,15)
        self.total.setValue(3)
        self.required.setRange(1,3)
        self.required.setValue(2)
        self.total.valueChanged.connect(self.required.setMaximum)
        line.addWidget(QLabel("Нужно подписей"))
        line.addWidget(self.required)
        line.addWidget(QLabel("из"))
        line.addWidget(self.total)
        line.addStretch()
        options.addWidget(counts)
        counts.setVisible(False)
        self.multi.toggled.connect(counts.setVisible)
        self.addPage(types)
        method = QWizardPage()
        method.setTitle("Источник ключей")
        method.setSubTitle(f"Кошелёк Pepecoin {network} · стандарт BIP39")
        layout = QVBoxLayout(method)
        self.new = QRadioButton("Создать новую сид-фразу")
        self.new.setChecked(True)
        layout.addWidget(self.new)
        self.restore = QRadioButton("У меня уже есть сид-фраза")
        self.master = QRadioButton("Использовать мастер-ключ")
        self.hardware = QRadioButton("Использовать аппаратный кошелёк Ledger")
        for radio in (self.restore,self.master,self.hardware):
            layout.addWidget(radio)
        self.addPage(method)
        seed = QWizardPage()
        seed.setTitle("Сид-фраза кошелька")
        self.seedpage = seed
        layout = QVBoxLayout(seed)
        self.words = QPlainTextEdit()
        layout.addWidget(self.words)
        self.pair_button = W.QPushButton("Подключить Ledger")
        self.pair_button.clicked.connect(self.pair_ledger)
        layout.addWidget(self.pair_button)
        self.addPage(seed)
        cos = QWizardPage()
        cos.setTitle("Ключи участников")
        self.cos_page = cos
        grid = QFormLayout(cos)
        self.own_pub = QLineEdit()
        self.own_pub.setReadOnly(True)
        self.cos = QPlainTextEdit()
        grid.addRow("Ваш публичный ключ", self.own_pub)
        grid.addRow("Публичные ключи остальных", self.cos)
        self.addPage(cos)
        pw = QWizardPage()
        pw.setTitle("Пароль кошелька")
        self.pwpage = pw
        form = QFormLayout(pw)
        self.pw, self.again = password_input(), password_input()
        form.addRow("Пароль", self.pw)
        self.repeat_label = QLabel("Ещё раз")
        form.addRow(self.repeat_label, self.again)
        self.addPage(pw)
        self.currentIdChanged.connect(self.setup_page)
        self.generated = ""
        self.last_method = None
        self.future = None
        self.pair_timer = QtCore.QTimer(self)
        self.pair_timer.timeout.connect(self.finish_pair)

    def path(self):
        name = self.name.currentText().strip()
        if not name or name.endswith((".", " ")) or any(c in '<>:"/\\|?*' or ord(c) < 32 for c in name):
            raise ValueError("Введите допустимое имя файла.")
        path = self.directory / name
        if isreserved(name) or path.is_dir():
            raise ValueError("Введите допустимое имя файла.")
        legacy = self.directory / (name + ".pepwallet")
        if not path.exists() and legacy.is_file():
            return legacy
        return path

    def mode(self):
        if self.imported.isChecked():
            return "import"
        if self.hardware.isChecked():
            return "ledger"
        if self.master.isChecked():
            return "master"
        return "create" if self.new.isChecked() else "restore"

    def key_document(self, cosigners=True):
        mode = self.mode()
        value = self.words.toPlainText().strip()
        if mode == "import":
            return {"kind":"import", "entries":value.split()}
        if mode in ("create","restore"):
            doc = {"words":" ".join(value.lower().split())}
        else:
            doc = {"master":value}
            if mode=="ledger":
                doc["ledger"] = True
        if self.multi.isChecked() and cosigners:
            others = self.cos.toPlainText().split()
            if len(others) != self.total.value()-1:
                raise ValueError(f"Введите {self.total.value()-1} публичных ключей остальных участников.")
            doc.update(kind="multisig", threshold=self.required.value(), cosigners=others)
        return doc

    def nextId(self):
        idx = self.currentId()
        if idx == 0:
            try:
                return 5 if self.path().is_file() else 1
            except ValueError:
                return 1
        if idx == 1 and self.imported.isChecked():
            return 3
        if idx == 3:
            return 4 if self.multi.isChecked() else 5
        return super().nextId()

    def setup_page(self, idx):
        if idx == 3:
            mode = self.mode()
            title, hint = {
                "import":("Импорт адресов или ключей", "Введите адреса PEP для наблюдения или WIF-ключи для подписи. По одному в строке."),
                "ledger":("Аппаратный кошелёк", "Откройте Pepecoin на Ledger, нажмите «Подключить» и подтвердите адрес на устройстве."),
                "master":("Мастер-ключ", "Введите ключ счёта xpub/dgub для наблюдения или xprv/dgpv для подписи. От этого ключа выводятся ветви /0 и /1."),
                "create":("Новая сид-фраза", "Запишите слова по порядку и сохраните их."),
                "restore":("Восстановление", "Введите слова BIP39 в исходном порядке."),
            }[mode]
            self.seedpage.setTitle(title)
            self.seedpage.setSubTitle(hint)
            if mode != self.last_method:
                self.words.clear()
                self.last_method = mode
            self.words.setReadOnly(mode in ("ledger","create"))
            self.pair_button.setVisible(mode=="ledger")
            if mode=="create":
                self.generated = self.generated or create_phrase()
                self.words.setPlainText(self.generated)
        if idx == 4:
            self.own_pub.setText(public(own_account(self.key_document(False))).to_base58())
            self.cos_page.setSubTitle(f"Передайте свой публичный ключ участникам. Введите остальные {self.total.value()-1} по одному в строке.")
        if idx == 5:
            exists = self.path().is_file()
            self.pwpage.setSubTitle("Введите пароль для открытия файла." if exists else "Пароль шифрует файл на этом компьютере.")
            self.again.setVisible(not exists)
            self.repeat_label.setVisible(not exists)
            self.pw.setFocus()

    def pair_ledger(self):
        from pep_ledger import pair
        if self.future is not None:
            return
        pool = ThreadPoolExecutor(max_workers=1)
        self.future = pool.submit(pair)
        pool.shutdown(wait=False)
        self.pair_button.setEnabled(False)
        self.pair_timer.start(150)

    def finish_pair(self):
        if not self.future.done():
            return
        self.pair_timer.stop()
        self.pair_button.setEnabled(True)
        future, self.future = self.future, None
        try:
            key = future.result()
            if self.currentId()==3 and self.mode()=="ledger":
                self.words.setPlainText(key)
        except Exception as exc:
            QMessageBox.warning(self, "Ledger", str(exc))

    def validateCurrentPage(self):
        try:
            path = self.path()
            idx = self.currentId()
            if idx in (3,4):
                KeySet(self.key_document(idx==4))
            if idx == 5:
                file_lock = QtCore.QLockFile(str(path.resolve()) + ".pep-lock")
                if not file_lock.tryLock(0):
                    raise ValueError("Этот файл кошелька уже открыт в другом окне.")
                if path.is_file():
                    self.wallet = Wallet.open(path, self.pw.text(), self.network)
                else:
                    if self.pw.text() != self.again.text():
                        raise ValueError("Пароли не совпадают.")
                    self.wallet = Wallet.create_with_keys(path,self.key_document(),self.pw.text(),self.network)
                self.wallet.file_lock = file_lock
            return True
        except Exception as exc:
            QMessageBox.warning(self, "Кошелёк", str(exc))
            return False
