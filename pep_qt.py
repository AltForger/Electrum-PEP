"""Qt desktop interface for Electrum-PEP mainnet."""
from pep_i18n import set_ui_language, tr
import argparse
from datetime import datetime
import os
from pathlib import Path
from queue import Empty, Queue
import shutil
import sys
from embit.psbt import PSBT
import pep_psbt
from wallet_keys import public
from threading import Thread
import time
from urllib.parse import urlencode

import qrcode
from PyQt6 import QtCore, QtGui, QtWidgets as W
from PyQt6.QtCore import Qt
from desktop_wallet import (COIN, Wallet, broadcast, coins, destination_script, discover,
                            estimated_fee, parse_amount, prepare, seen)
from qt_wallet_dialogs import DIALOG_FLAGS, TITLE_BAR, PEP_LOGO, VERSION, NetworkDialog, WalletWizard, about, icon, info_table, preferences, show_startup_notice
from mainnet_electrum import SERVERS, WalletConnection, server_endpoint
from pep_wallet import NETWORKS


def button(text, callback, image=None):
    b = W.QPushButton(text)
    if image:
        b.setIcon(icon(image))
    b.clicked.connect(lambda: callback())
    return b


def table(headers, stretch):
    t = W.QTreeWidget()
    t.setColumnCount(len(headers))
    t.setHeaderLabels(headers)
    t.setRootIsDecorated(False)
    t.setUniformRowHeights(True)
    t.setSelectionBehavior(W.QAbstractItemView.SelectionBehavior.SelectRows)
    t.header().setStretchLastSection(False)
    for i in range(len(headers)):
        t.header().setSectionResizeMode(i, W.QHeaderView.ResizeMode.Stretch if i == stretch else W.QHeaderView.ResizeMode.ResizeToContents)
    t.setAlternatingRowColors(True)
    return t


def row(*widgets, right=False):
    layout = W.QHBoxLayout()
    if right:
        layout.addStretch()
    for widget in widgets:
        layout.addWidget(widget)
    if not right:
        layout.addStretch()
    return layout


def datum(text):
    return W.QTreeWidgetItem([str(v) for v in text])


def external(url):
    QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))


def stamp(t):
    return datetime.fromtimestamp(t).strftime("%d.%m.%Y %H:%M") if t else tr("Pending")


class MainWindow(W.QMainWindow):
    def __init__(self, wallet, directory, settings):
        super().__init__(None, Qt.WindowType.Window | TITLE_BAR)
        self.wallet, self.directory, self.settings = wallet, directory, settings
        self.explorer = "https://pepeblocks.com"
        manual = None
        if not settings.value("server/auto", True, bool):
            manual = server_endpoint(settings.value("server/host", SERVERS[0], str), settings.value("server/port", 50002, int))
        self.connection = WalletConnection(manual)
        self.displayed_status = None
        self.snapshot = None
        self.last_sync = self.last_error = ""
        self.jobs = Queue()
        self.busy = False
        self.tabs = W.QTabWidget()
        self.setCentralWidget(self.tabs)
        self.setMinimumSize(680, 430)
        self.resize(905, 540)
        self.setWindowIcon(QtGui.QIcon(str(PEP_LOGO)))
        self.setWindowTitle(f"Electrum-PEP {VERSION}  -  {Path(wallet.storage.path).name}  [PEP {wallet.network}]")
        self.history = table([tr("Date"), tr("Description"), tr("Amount (PEP)"), tr("Balance (PEP)")], 1)
        self.history.itemDoubleClicked.connect(lambda item, _: self.transaction_details(item))
        self.addresses = table([tr("Type"), tr("Address"), tr("Index"), tr("Balance (PEP)"), tr("Activity")], 1)
        self.outputs = table([tr("Output"), tr("Address"), tr("Amount (PEP)")], 1)
        self.contacts = table([tr("Name"), tr("Address")], 1)
        self.contacts.itemDoubleClicked.connect(lambda item, _: self.pay_to(item.text(1)))
        self.pages = {}
        for key, name, widget in [
            ("history", tr("History"), self.history), ("send", tr("Send"), self.send_page()),
            ("receive", tr("Receive"), self.receive_page()), ("addresses", tr("Addresses"), self.addresses),
            ("coins", tr("Coins"), self.outputs), ("contacts", tr("Contacts"), self.contacts_page()),
            ("console", tr("Console"), self.console_page()),
        ]:
            i = self.tabs.addTab(widget, icon(f"tab_{key}.png"), name)
            self.pages[key] = i
        self.addresses.itemDoubleClicked.connect(lambda item, _: self.copy(item.text(1)))
        self.outputs.itemDoubleClicked.connect(lambda item, _: external(self.explorer + "/tx/" + item.text(0).split(":")[0]))
        self.status_text = W.QLabel(tr("Synchronizing…"))
        coin_logo = W.QLabel()
        coin_logo.setPixmap(QtGui.QIcon(str(PEP_LOGO)).pixmap(24,24))
        coin_logo.setToolTip("Pepecoin · PEP mainnet")
        self.statusBar().addWidget(coin_logo)
        self.statusBar().addWidget(self.status_text, 1)
        for png, tip, cb in [("lock.png", tr("Lock wallet"), self.lock_wallet),
                             ("preferences.png", tr("Preferences"), self.prefs),
                             ("seed.png", tr("Seed phrase"), self.show_seed),
                             ("status_waiting.png", tr("Network"), self.network_info)]:
            if png == "seed.png" and not wallet.document.get("words"):
                continue
            b = W.QToolButton()
            b.setToolTip(tip)
            b.setAutoRaise(True)
            b.setIcon(icon(png))
            b.clicked.connect(lambda _, fn=cb: fn())
            self.statusBar().addPermanentWidget(b)
            self.network_indicator = b
        self.build_menu()
        self.render_contacts()
        self.render_saved()
        self.poll = QtCore.QTimer(self)
        self.poll.timeout.connect(self.drain)
        self.poll.start(80)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.keepalive = QtCore.QTimer(self)
        self.keepalive.timeout.connect(self.ping)
        self.keepalive.start(25000)
        self.apply_settings()
        geometry = settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        QtCore.QTimer.singleShot(0, self.refresh)

    def action(self, menu, title, callback, shortcut=None):
        a = menu.addAction(title)
        a.triggered.connect(lambda _: callback())
        if shortcut:
            a.setShortcut(shortcut)
        return a

    def build_menu(self):
        bar = self.menuBar()
        file = bar.addMenu(tr("&File"))
        self.action(file, tr("New / Restore…"), self.switch_wallet, "Ctrl+N")
        self.action(file, tr("Open…"), self.open_file, "Ctrl+O")
        self.action(file, tr("Open PSBT transaction…"), self.open_psbt)
        self.action(file, tr("Save backup…"), self.backup)
        file.addSeparator()
        self.action(file, tr("Exit"), self.close, "Ctrl+Q")
        wallet_menu = bar.addMenu(tr("&Wallet"))
        self.action(wallet_menu, tr("Information"), self.wallet_info)
        if self.wallet.document.get("words"):
            self.action(wallet_menu, tr("Seed phrase"), self.show_seed)
        self.action(wallet_menu, tr("Lock"), self.lock_wallet, "Ctrl+L")
        view = bar.addMenu(tr("&View"))
        for key in ("addresses", "coins", "contacts", "console"):
            idx = self.pages[key]
            a = view.addAction(self.tabs.tabText(idx))
            a.setCheckable(True)
            visible = self.settings.value("tabs/" + key, False, bool)
            self.tabs.setTabVisible(idx, visible)
            a.setChecked(visible)
            def toggle(checked, index=idx, name=key):
                self.tabs.setTabVisible(index, checked)
                self.settings.setValue("tabs/" + name, checked)
            a.toggled.connect(toggle)
        tools = bar.addMenu(tr("&Tools"))
        self.action(tools, tr("Preferences…"), self.prefs)
        self.action(tools, tr("Network…"), self.network_info)
        self.action(tools, tr("Refresh"), self.refresh, "F5")
        help_menu = bar.addMenu(tr("&Help"))
        self.action(help_menu, tr("About Electrum-PEP"), lambda: about(self))
        self.action(help_menu, tr("About Qt"), lambda: W.QMessageBox.aboutQt(self, tr("About Qt")))

    def send_page(self):
        page = W.QWidget()
        box = W.QVBoxLayout(page)
        form = W.QFormLayout()
        self.payto, self.description, self.amount = W.QLineEdit(), W.QLineEdit(), W.QLineEdit()
        self.payto.setPlaceholderText(tr("Pepecoin {network} address").format(network=self.wallet.network))
        form.addRow(tr("Recipient"), self.payto)
        form.addRow(tr("Description"), self.description)
        form.addRow(tr("Amount"), row(self.amount, W.QLabel("PEP"), button(tr("Max"), self.maximum)))
        box.addLayout(form)
        self.pay_button = button(tr("Pay…"), self.pay)
        box.addLayout(row(button(tr("Clear"), self.clear_send), button(tr("Save"), self.save_draft), self.pay_button, right=True))
        box.addWidget(W.QLabel(tr("Payment queue")))
        self.drafts = table([tr("Description"), tr("Recipient"), tr("Amount (PEP)")], 1)
        self.drafts.itemDoubleClicked.connect(lambda item, _: self.load_draft(item))
        self.drafts.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.drafts.customContextMenuRequested.connect(self.draft_menu)
        box.addWidget(self.drafts, 1)
        return page

    def receive_page(self):
        page = W.QWidget()
        box = W.QVBoxLayout(page)
        top = W.QHBoxLayout()
        left = W.QVBoxLayout()
        form = W.QFormLayout()
        self.recv_desc, self.recv_amount, self.recv_address = W.QLineEdit(), W.QLineEdit(), W.QLineEdit()
        self.recv_address.setReadOnly(True)
        self.recv_address.setText(self.wallet.address(0, 0))
        self.expiry = W.QComboBox()
        for label, seconds in [(tr("1 day"), 86400), (tr("1 hour"), 3600), (tr("1 week"), 604800), (tr("No expiry"), 0)]:
            self.expiry.addItem(label, seconds)
        form.addRow(tr("Description"), self.recv_desc)
        form.addRow(tr("Requested amount"), row(self.recv_amount, W.QLabel("PEP")))
        form.addRow(tr("Expires after"), self.expiry)
        form.addRow(tr("Address"), self.recv_address)
        left.addLayout(form)
        actions = row(button(tr("Copy"), lambda: self.copy(self.recv_address.text()), "copy.png"),
                      button(tr("Create request"), self.new_request))
        left.addLayout(actions)
        top.addLayout(left, 1)
        self.qr = W.QLabel()
        self.qr.setFixedSize(155, 155)
        self.qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top.addWidget(self.qr)
        box.addLayout(top)
        box.addWidget(W.QLabel(tr("Payment requests")))
        self.requests = table([tr("Date"), tr("Description"), tr("Amount (PEP)"), tr("Status")], 1)
        self.requests.itemDoubleClicked.connect(lambda item, _: self.show_request(item))
        box.addWidget(self.requests)
        self.set_qr(self.recv_address.text())
        return page

    def set_qr(self, value):
        code = qrcode.QRCode(border=4)
        code.add_data(value)
        grid = code.get_matrix()
        side = len(grid)
        image = QtGui.QImage(side, side, QtGui.QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.white)
        for y, line in enumerate(grid):
            for x, on in enumerate(line):
                if on:
                    image.setPixel(x, y, 0xff000000)
        scale = max(1, 155 // side)
        self.qr.setPixmap(QtGui.QPixmap.fromImage(image).scaled(side * scale, side * scale))

    def contacts_page(self):
        page = W.QWidget()
        box = W.QVBoxLayout(page)
        box.addWidget(self.contacts)
        box.addLayout(row(button(tr("New contact…"), self.add_contact), button(tr("Delete"), self.remove_contact)))
        return page

    def console_page(self):
        page = W.QWidget()
        box = W.QVBoxLayout(page)
        self.console = W.QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setPlainText(tr("Electrum-PEP {network}\nType help to list available commands").format(network=self.wallet.network))
        self.command = W.QLineEdit()
        self.command.returnPressed.connect(self.run_command)
        box.addWidget(self.console)
        box.addWidget(self.command)
        return page

    def fmt(self, amount):
        s = coins(amount)
        minimum = self.settings.value("zeros", 0, int)
        if minimum:
            whole, _, fraction = s.partition(".")
            s = whole + "." + fraction.ljust(minimum, "0")
        return s

    def copy(self, text):
        W.QApplication.clipboard().setText(text)
        self.statusBar().showMessage(tr("Copied"), 2000)

    def run_job(self, msg, work, done):
        if self.busy:
            return
        self.busy = True
        self.pay_button.setEnabled(False)
        self.statusBar().showMessage(msg)
        def worker():
            try:
                self.jobs.put((done, work()))
            except Exception as exc:
                self.jobs.put((self.failed, exc))
        Thread(target=worker, daemon=True).start()

    def drain(self):
        status = self.connection.status
        if self.displayed_status != status:
            self.displayed_status = status
            state, peer, _ = status
            image = {tr("Connected · TLS"): "status_connected.png", tr("Connecting…"): "status_waiting.png"}
            self.network_indicator.setIcon(icon(image.get(state, "status_disconnected.png")))
            self.network_indicator.setToolTip(tr("Network · ") + (f"{state} {peer}" if peer else state))
        try:
            callback, result = self.jobs.get_nowait()
        except Empty:
            return
        self.busy = False
        self.pay_button.setEnabled(True)
        self.statusBar().clearMessage()
        try:
            callback(result)
        except Exception as e:
            self.failed(e)

    def failed(self, exc):
        self.last_error = str(exc)
        self.statusBar().showMessage(tr("Error: ") + self.last_error)
        self.statusBar().setToolTip(self.last_error)
        self.pay_button.setText(tr("Retry payment") if self.wallet.document.get("pending") else tr("Pay…"))

    def refresh(self):
        if self.busy or W.QApplication.activeModalWidget():
            return
        pending = self.wallet.document.get("pending")
        def fetch():
            snap = discover(self.wallet, history=True, run=self.connection.run)
            return snap, pending and seen(pending, run=self.connection.run)
        def update(result):
            self.snapshot, observed = result
            if observed:
                self.wallet.document["last"] = self.wallet.document.pop("pending")
                self.wallet.save()
            self.last_sync, self.last_error = stamp(time.time()), ""
            self.statusBar().setToolTip("")
            self.paint_data()
        self.run_job(tr("Synchronizing…"), fetch, update)

    def paint_data(self):
        s = self.snapshot
        if not s:
            return
        balance = tr("Balance: ") + self.fmt(s.confirmed) + " PEP"
        if s.pending:
            balance += "  [" + self.fmt(s.pending) + tr(" PEP unconfirmed]")
        self.status_text.setText(balance)
        self.pay_button.setText(tr("Retry payment") if self.wallet.document.get("pending") else tr("Pay…"))
        self.history.clear()
        owned = {a[2] for a in s.addresses}
        running = s.confirmed + s.pending
        labels = self.wallet.document.get("labels", {})
        for tx in s.transactions:
            def mine(output):
                return bool(owned.intersection(output.get("addresses") or ()))
            delta = sum(int(v["value"]) for v in tx["vout"] if mine(v)) - sum(int(v["value"]) for v in tx["vin"] if mine(v))
            confirms = int(tx.get("confirmations", 0))
            status = stamp(tx.get("blockTime")) if confirms else tr("Unconfirmed")
            label = labels.get(tx["txid"]) or (tr("Receive") if delta > 0 else tr("Send"))
            item = datum([status, label, ("+" if delta > 0 else "") + self.fmt(delta), self.fmt(running)])
            item.setIcon(0, icon("confirmed.png" if confirms >= 6 else f"clock{confirms}.png" if confirms else "unconfirmed.png"))
            item.setToolTip(0, tr("Confirmations: {count}").format(count=confirms))
            item.setData(0, Qt.ItemDataRole.UserRole, tx)
            if delta < 0:
                item.setForeground(2, QtGui.QColor("#bd3838"))
            self.history.addTopLevelItem(item)
            running -= delta
        self.addresses.clear()
        address_rows = list(s.addresses)
        for branch in (() if self.wallet.keys.entries else (0, 1)):
            child = s.next_indices[branch]
            address_rows.append((branch, child, self.wallet.address(branch, child), 0, 0))
        for branch, idx, addr, value, count in sorted(address_rows):
            self.addresses.addTopLevelItem(datum([tr("Change") if branch else tr("Receive"), addr, idx, self.fmt(value), count]))
        self.outputs.clear()
        for utxo in s.utxos:
            self.outputs.addTopLevelItem(datum([f"{utxo.txid}:{utxo.index}", self.wallet.address(utxo.branch, utxo.child), self.fmt(utxo.value)]))
        self.recv_address.setText(self.wallet.address(0, self.receive_display_index()))
        self.set_qr(self.recv_address.text())
        self.render_saved()

    def rate(self):
        return self.settings.value("fee_rate", COIN // 100, int)

    def maximum(self):
        if self.snapshot and self.snapshot.utxos:
            utxos = self.snapshot.utxos
            self.amount.setText(coins(max(0, sum(c.value for c in utxos) - estimated_fee(len(utxos), self.rate(), self.wallet.keys.input_bytes()))))

    def clear_send(self):
        for box in (self.payto, self.description, self.amount):
            box.clear()

    def pay_to(self, addr):
        self.tabs.setCurrentIndex(self.pages["send"])
        self.payto.setText(addr)
        self.amount.setFocus()

    def send_fields(self):
        addr = self.payto.text().strip()
        destination_script(addr, self.wallet.network)
        return addr, parse_amount(self.amount.text()), self.description.text().strip()

    def pay(self):
        if self.busy:
            return
        pending = self.wallet.document.get("pending")
        if pending:
            if W.QMessageBox.question(self, tr("Retry broadcast"), f"{coins(pending['amount'])} PEP → {pending['address']}\nTXID: {pending['txid']}") == W.QMessageBox.StandardButton.Yes:
                self.transmit(pending)
            return
        try:
            addr, amount, desc = self.send_fields()
        except ValueError as exc:
            W.QMessageBox.warning(self, tr("Payment"), str(exc))
            return
        rate = self.rate()
        def confirm(plan):
            if "psbt" in plan:
                self.review_psbt(PSBT.from_base64(plan["psbt"]), desc)
                return
            text = tr("Network: Pepecoin {network}\nRecipient: {address}\nAmount: {amount} PEP\nFee: {fee} PEP").format(network=self.wallet.network, address=addr, amount=coins(amount), fee=coins(plan['fee']))
            if W.QMessageBox.question(self, tr("Confirm payment"), text) != W.QMessageBox.StandardButton.Yes:
                return
            self.accept_payment(plan, desc)
        self.run_job(tr("Checking inputs and fee…"), lambda: prepare(self.wallet, discover(self.wallet, run=self.connection.run), addr, amount, rate, run=self.connection.run), confirm)

    def accept_payment(self, plan, desc):
        document = self.wallet.document
        if document.get("pending"):
            raise ValueError(tr("Complete the previous payment first."))
        document["pending"] = plan
        try:
            if desc:
                document.setdefault("labels", {})[plan["txid"]] = desc
            self.wallet.save()
        except Exception:
            document.pop("pending", None)
            raise
        self.transmit(plan)

    def open_psbt(self):
        if self.busy:
            self.statusBar().showMessage(tr("Wait for the current operation to finish."), 2500)
            return
        path, _ = W.QFileDialog.getOpenFileName(self,tr("Open transaction"), "", "PSBT (*.psbt)")
        if path:
            try:
                with open(path,"rb") as stream:
                    data = stream.read(8_000_001)
                if len(data)>8_000_000:
                    raise ValueError(tr("File is too large."))
                self.review_psbt(PSBT.parse(data))
            except Exception as exc:
                W.QMessageBox.warning(self,"PSBT",str(exc))

    def review_psbt(self, p, desc=""):
        outputs, fee = pep_psbt.inspect(self.wallet,p)
        lines = [f"{addr}   {coins(amount)} PEP" for addr,amount in outputs]
        review = W.QDialog(self,DIALOG_FLAGS)
        review.setWindowTitle(tr("Review transaction · PEP mainnet"))
        layout = W.QVBoxLayout(review)
        layout.addWidget(W.QLabel(tr("All outputs, including change")))
        listing = W.QPlainTextEdit("\n".join(lines))
        listing.setReadOnly(True)
        layout.addWidget(listing)
        layout.addWidget(W.QLabel(tr("Fee: {fee} PEP").format(fee=coins(fee))))
        layout.addLayout(row(button(tr("Cancel"),review.reject),
                             button(tr("Continue") if self.wallet.keys.watch else tr("Sign…"),review.accept)))
        review.resize(580,280)
        if not review.exec():
            return
        def ready(plan):
            if "psbt" in plan:
                path,_ = W.QFileDialog.getSaveFileName(self,tr("Save for the next cosigner"),"payment.psbt","PSBT (*.psbt)")
                if path:
                    Path(path).write_bytes(PSBT.from_base64(plan["psbt"]).serialize())
                return
            if W.QMessageBox.question(self, tr("Broadcast the signed transaction?"),
                                      "\n".join(lines)+tr("\nFee: {fee} PEP").format(fee=coins(fee))) == W.QMessageBox.StandardButton.Yes:
                self.accept_payment(plan,desc)
        self.run_job(tr("Signing transaction…"),lambda:pep_psbt.sign(self.wallet,p),ready)

    def transmit(self, plan):
        def complete(_):
            self.clear_send()
            self.pay_button.setText(tr("Retry payment"))
            W.QMessageBox.information(self, tr("Transaction broadcast"), "TXID\n" + plan["txid"])
            self.refresh()
        self.run_job(tr("Broadcasting transaction to PEP {network}…").format(network=self.wallet.network), lambda: broadcast(plan, self.wallet.network, run=self.connection.run), complete)

    def save_draft(self):
        try:
            addr, amount, desc = self.send_fields()
        except ValueError as e:
            W.QMessageBox.warning(self, tr("Draft"), str(e))
            return
        draft = {"address": addr, "amount": amount, "description": desc}
        drafts = self.wallet.document.setdefault("drafts", [])
        if draft not in drafts:
            drafts.append(draft)
            self.wallet.save()
        self.render_saved()

    def load_draft(self, item):
        draft = item.data(0, Qt.ItemDataRole.UserRole)
        self.payto.setText(draft["address"])
        self.amount.setText(coins(draft["amount"]))
        self.description.setText(draft["description"])

    def draft_menu(self, pos):
        item = self.drafts.itemAt(pos)
        if not item:
            return
        menu = W.QMenu(self)
        load = menu.addAction(tr("Open draft"))
        remove = menu.addAction(tr("Delete draft"))
        choice = menu.exec(self.drafts.viewport().mapToGlobal(pos))
        if choice is load:
            self.load_draft(item)
        elif choice is remove:
            self.wallet.document["drafts"].remove(item.data(0, Qt.ItemDataRole.UserRole))
            self.wallet.save()
            self.render_saved()

    def receive_display_index(self):
        limit = len(self.wallet.keys.entries) if self.wallet.keys.entries else self.snapshot.next_indices[0] + 20
        return min(self.receive_index(), limit-1)

    def receive_index(self):
        used = self.snapshot.next_indices[0] if self.snapshot else 0
        reserved = [r["index"] for r in self.wallet.document.get("requests", [])]
        return max([used] + [i + 1 for i in reserved])

    def new_request(self):
        if not self.snapshot:
            W.QMessageBox.information(self, tr("Receive"), tr("Wait for address synchronization to finish."))
            return
        try:
            amount = parse_amount(self.recv_amount.text()) if self.recv_amount.text().strip() else 0
            idx = self.receive_index()
            if self.wallet.keys.entries and idx >= len(self.wallet.keys.entries):
                raise ValueError(tr("All imported addresses have already been issued. Copy an existing address to receive a payment."))
            if not self.wallet.keys.entries and idx - self.snapshot.next_indices[0] >= 20:
                raise ValueError(tr("Twenty consecutive unused addresses have already been issued. Use an existing payment request."))
        except ValueError as e:
            W.QMessageBox.warning(self, tr("Request"), str(e))
            return
        request = {"time": int(time.time()), "index": idx, "address": self.wallet.address(0, idx),
                   "description": self.recv_desc.text().strip(), "amount": amount, "expiry": self.expiry.currentData()}
        self.wallet.document.setdefault("requests", []).append(request)
        self.wallet.save()
        self.render_saved()
        self.show_request(self.requests.topLevelItem(0))

    def show_request(self, item):
        request = item.data(0, Qt.ItemDataRole.UserRole)
        self.recv_address.setText(request["address"])
        params = {}
        if request["amount"]:
            params["amount"] = coins(request["amount"])
        if request["description"]:
            params["label"] = request["description"]
        suffix = "?" + urlencode(params) if params else ""
        self.set_qr("pepecoin:" + request["address"] + suffix)

    def render_saved(self):
        self.drafts.clear()
        for d in self.wallet.document.get("drafts", []):
            item = datum([d["description"], d["address"], self.fmt(d["amount"])])
            item.setData(0, Qt.ItemDataRole.UserRole, d)
            self.drafts.addTopLevelItem(item)
        self.requests.clear()
        for req in reversed(self.wallet.document.get("requests", [])):
            paid = 0
            pending = 0
            for tx in self.snapshot.transactions if self.snapshot else []:
                outputs = sum(int(o["value"]) for o in tx["vout"] if req["address"] in (o.get("addresses") or ()))
                if tx.get("confirmations"):
                    paid += outputs
                else:
                    pending += outputs
            target = max(1, req["amount"])
            expired = req["expiry"] and time.time() > req["time"] + req["expiry"]
            status = (tr("Paid") if paid >= target else tr("Unconfirmed") if paid + pending >= target else
                      tr("Partially paid") if paid + pending else tr("Expired") if expired else tr("Awaiting payment"))
            item = datum([stamp(req["time"]), req["description"], self.fmt(req["amount"]) if req["amount"] else tr("Any amount"), status])
            item.setData(0, Qt.ItemDataRole.UserRole, req)
            self.requests.addTopLevelItem(item)

    def render_contacts(self):
        self.contacts.clear()
        for addr, name in sorted(self.wallet.document.get("contacts", {}).items(), key=lambda x: x[1].casefold()):
            self.contacts.addTopLevelItem(datum([name, addr]))

    def add_contact(self):
        name, ok = W.QInputDialog.getText(self, tr("New contact"), tr("Name"))
        if not ok or not name.strip():
            return
        addr, ok = W.QInputDialog.getText(self, tr("New contact"), tr("PEP {network} address").format(network=self.wallet.network))
        if not ok:
            return
        try:
            destination_script(addr.strip(), self.wallet.network)
        except ValueError as e:
            W.QMessageBox.warning(self, tr("Address"), str(e))
            return
        self.wallet.document.setdefault("contacts", {})[addr.strip()] = name.strip()
        self.wallet.save()
        self.render_contacts()

    def remove_contact(self):
        item = self.contacts.currentItem()
        if item and W.QMessageBox.question(self, tr("Contacts"), tr("Delete contact “{name}”?").format(name=item.text(0))) == W.QMessageBox.StandardButton.Yes:
            del self.wallet.document["contacts"][item.text(1)]
            self.wallet.save()
            self.render_contacts()

    def transaction_details(self, item):
        tx = item.data(0, Qt.ItemDataRole.UserRole)
        dlg = W.QDialog(self, DIALOG_FLAGS)
        dlg.setWindowTitle(tr("Transaction"))
        box = W.QVBoxLayout(dlg)
        form = W.QFormLayout()
        box.addLayout(form)
        txid = W.QLineEdit(tx["txid"])
        txid.setReadOnly(True)
        form.addRow("TXID", txid)
        form.addRow(tr("Confirmations"), W.QLabel(str(tx.get("confirmations", 0))))
        form.addRow(tr("Block"), W.QLabel(str(tx.get("blockHeight", "—"))))
        form.addRow(tr("Fee"), W.QLabel(self.fmt(int(tx["fees"])) + " PEP" if tx.get("fees") is not None else "—"))
        label = W.QLineEdit(self.wallet.document.get("labels", {}).get(tx["txid"], ""))
        form.addRow(tr("Description"), label)
        outputs = table([tr("Recipient address"), tr("Amount (PEP)")], 0)
        for out in tx["vout"]:
            outputs.addTopLevelItem(datum([", ".join(out.get("addresses") or []), self.fmt(int(out["value"]))]))
        box.addWidget(outputs)
        box.addLayout(row(button(tr("View in block explorer ↗"), lambda: external(self.explorer + "/tx/" + tx["txid"])),
                          button(tr("Save description"), dlg.accept), button(tr("Close"), dlg.reject), right=True))
        dlg.resize(655, 345)
        if dlg.exec():
            self.wallet.document.setdefault("labels", {})[tx["txid"]] = label.text().strip()
            self.wallet.save()
            self.paint_data()

    def wallet_info(self):
        keys = self.wallet.keys
        names = {"standard":tr("Standard"), "multisig":tr("Multisig {required} of {total}").format(required=keys.threshold, total=len(keys.accounts)),
                 "import":tr("Imported")}
        fields = [(tr("File"), self.wallet.storage.path), (tr("Network"),"Pepecoin mainnet"),
                  (tr("Type"),names[keys.kind]),(tr("Signing"),"Ledger" if keys.ledger else tr("Watch-only") if keys.watch else tr("Local")),
                  (tr("File encryption"),"Electrum BIE1")]
        if self.wallet.document.get("words") or keys.ledger:
            fields.append((tr("Account derivation path"),"m/44'/3434'/0'"))
        for i,k in enumerate(keys.accounts):
            fields.append((tr("Your public key") if i==0 else tr("Cosigner {number}").format(number=i + 1),public(k).to_base58()))
        info_table(self,tr("Wallet information"),fields)

    def network_info(self):
        dialog = self.findChild(NetworkDialog)
        if dialog is None:
            dialog = NetworkDialog(self)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def set_server(self, manual):
        self.connection.close()
        self.connection = WalletConnection(manual)
        self.last_sync = self.last_error = ""
        self.settings.setValue("server/auto", manual is None)
        if manual:
            self.settings.setValue("server/host", manual[0])
            self.settings.setValue("server/port", manual[1])
        self.settings.sync()
        self.refresh()

    def ping(self):
        if not self.busy and self.connection.client and not W.QApplication.activeModalWidget():
            peer = self.connection.status[1]
            def complete(_):
                if peer != self.connection.status[1]:
                    self.refresh()
            self.run_job(tr("Checking connection…"), lambda: self.connection.run(lambda c: c.call("server.ping", [])), complete)

    def prefs(self):
        if preferences(self, self.settings):
            self.apply_settings()
            self.paint_data()

    def apply_settings(self):
        for t in self.findChildren(W.QTreeWidget):
            t.setAlternatingRowColors(self.settings.value("alternating", True, bool))
        self.timer.start(1000 * self.settings.value("interval", 60, int))

    def show_seed(self):
        pw, ok = W.QInputDialog.getText(self, tr("Seed phrase"), tr("Wallet password"), W.QLineEdit.EchoMode.Password)
        if not ok:
            return
        try:
            words = Wallet.open(Path(self.wallet.storage.path), pw, self.wallet.network).document["words"]
        except ValueError as e:
            W.QMessageBox.warning(self, tr("Seed phrase"), str(e))
            return
        info_table(self, tr("BIP39 seed phrase"), [(tr("Words in order"), words)])

    def backup(self):
        path, _ = W.QFileDialog.getSaveFileName(self, tr("Backup"), str(Path.home() / f"{self.wallet.network}-backup"), tr("All files (*)"))
        if path:
            try:
                shutil.copy2(self.wallet.storage.path, path)
                self.statusBar().showMessage(tr("Encrypted backup saved"), 3500)
            except OSError as exc:
                W.QMessageBox.warning(self, tr("Backup"), str(exc))

    def switch_wallet(self, selected=None):
        if self.busy:
            self.statusBar().showMessage(tr("Wait for the current operation to finish."), 2500)
            return
        self.change_to = selected or "new"
        self.close()

    def open_file(self):
        path, _ = W.QFileDialog.getOpenFileName(self, tr("Open wallet"), str(self.directory), tr("All files (*)"))
        if path:
            self.switch_wallet(Path(path))

    def lock_wallet(self):
        self.switch_wallet(Path(self.wallet.storage.path))

    def run_command(self):
        command = self.command.text().strip().lower()
        self.command.clear()
        snap = self.snapshot
        commands = {"help": lambda: "getinfo · getbalance · listaddresses · listunspent · history · clear",
                    "getinfo": lambda: f"Electrum-PEP {VERSION} · {self.wallet.network} · {self.wallet.storage.path}",
                    "getbalance": lambda: self.status_text.text(),
                    "listaddresses": lambda: "\n".join(self.addresses.topLevelItem(i).text(1) for i in range(self.addresses.topLevelItemCount())),
                    "listunspent": lambda: "\n".join(f"{u.txid}:{u.index}  {self.fmt(u.value)} PEP" for u in snap.utxos) if snap else tr("Synchronizing"),
                    "history": lambda: "\n".join(t["txid"] for t in snap.transactions) if snap else tr("Synchronizing")}
        if command == "clear":
            self.console.clear()
            return
        self.console.appendPlainText(">>> " + command + "\n" + commands.get(command, lambda: tr("Unknown command. Type help."))())

    def closeEvent(self, event):
        self.timer.stop()
        self.poll.stop()
        self.keepalive.stop()
        self.connection.close()
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.sync()
        super().closeEvent(event)


def main(portable=False):
    parser = argparse.ArgumentParser()
    default = (Path(sys.executable).resolve().parent / "Electrum-PEP-data" if portable else
               Path(os.environ["LOCALAPPDATA"]) / "Electrum-PEP")
    parser.add_argument("--datadir", type=Path, default=default)
    args = parser.parse_args()
    root = args.datadir.resolve()
    app = W.QApplication([])
    app.setApplicationName("Electrum-PEP")
    app.setWindowIcon(QtGui.QIcon(str(PEP_LOGO)))
    translator = QtCore.QTranslator(app)
    ui_languages = QtCore.QLocale.system().uiLanguages()
    language = set_ui_language(ui_languages or [QtCore.QLocale.system().name()])
    ui_locale = QtCore.QLocale(language)
    app.setLayoutDirection(ui_locale.textDirection())
    if translator.load(ui_locale, "qtbase", "_", QtCore.QLibraryInfo.path(QtCore.QLibraryInfo.LibraryPath.TranslationsPath)):
        app.installTranslator(translator)
    folder = root / "mainnet"
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        W.QMessageBox.critical(None,tr("Wallet folder"),str(exc))
        return
    wallet_session(app, folder)


def wallet_session(app, root):
    lock = QtCore.QLockFile(str(root / "qt-instance.lock"))
    if not lock.tryLock(0):
        W.QMessageBox.information(None, "Electrum-PEP", tr("The mainnet wallet is already open."))
        return
    settings = QtCore.QSettings(str(root / "qt-settings.ini"), QtCore.QSettings.Format.IniFormat)
    directory = root / "wallets"
    selected = None
    while True:
        wizard = WalletWizard(selected.parent if isinstance(selected, Path) else directory,
                              selected if isinstance(selected, Path) else None, network="mainnet")
        if selected == "new":
            n = 1
            while (directory / f"wallet_{n}").exists() or (directory / f"wallet_{n}.pepwallet").exists():
                n += 1
            wizard.name.setCurrentText(f"wallet_{n}")
        if not wizard.exec():
            break
        window = MainWindow(wizard.wallet, directory, settings)
        window.show()
        if selected is None:
            show_startup_notice(settings, window)
        wizard.deleteLater()
        app.exec()
        window.wallet.file_lock.unlock()
        selected = getattr(window, "change_to", None)
        window.deleteLater()
        if not selected:
            break
    lock.unlock()


if __name__ == "__main__":
    main()
