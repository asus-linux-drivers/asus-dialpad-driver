"""Offline, non-executing help for the optional layout manager."""
from __future__ import annotations

from html import escape

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QSplitter, QTextBrowser, QToolButton, QVBoxLayout, QWidget,
)

from dialpad_i18n import language_preference, save_language_preference, tr

TOPICS = {
    topic: (f"help.topics.{topic}.title", f"help.topics.{topic}.body")
    for topic in (
        "overview", "library", "identity", "state", "rules", "functions",
        "actions", "action_type", "events", "modifier", "trigger", "duration",
        "command", "title", "icon", "unit", "value", "threshold",
        "conditional_icons", "geometry", "raw_json", "save_activate", "trust",
        "language", "troubleshooting",
    )
}


class HelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("managerHelp")
        self.setWindowTitle(tr("help.window.title"))
        self.resize(850, 600)
        layout = QVBoxLayout(self)
        self.search = QLineEdit(self)
        self.search.setObjectName("helpSearch")
        self.search.setPlaceholderText(tr("help.window.search_placeholder"))
        self.search.setAccessibleName(tr("help.window.search_accessible"))
        layout.addWidget(self.search)
        split = QSplitter(self)
        self.topics = QListWidget(split)
        self.topics.setObjectName("helpTopics")
        self.browser = QTextBrowser(split)
        self.browser.setObjectName("helpContent")
        self.browser.setOpenExternalLinks(False)
        self.browser.setOpenLinks(False)
        self.browser.setAccessibleName(tr("help.window.content_accessible"))
        split.setSizes([240, 610])
        layout.addWidget(split, 1)
        self.empty = QLabel(tr("help.window.no_results"), self)
        self.empty.hide()
        layout.addWidget(self.empty)
        for topic, (title, _body) in TOPICS.items():
            item = QListWidgetItem(tr(title))
            item.setData(Qt.ItemDataRole.UserRole, topic)
            self.topics.addItem(item)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
        self.search.textChanged.connect(self._filter)
        self.topics.currentItemChanged.connect(self._display)
        self.select_topic("overview")

    def _display(self, item, previous=None):
        if item is None:
            self.browser.clear()
            return
        title, body = TOPICS[item.data(Qt.ItemDataRole.UserRole)]
        paragraphs = "".join("<p>" + escape(part) + "</p>" for part in tr(body).split("\n\n"))
        self.browser.setHtml("<h2>" + escape(tr(title)) + "</h2>" + paragraphs)

    def _filter(self, text):
        text = text.casefold().strip()
        first = None
        for index in range(self.topics.count()):
            item = self.topics.item(index)
            topic = item.data(Qt.ItemDataRole.UserRole)
            title, body = TOPICS[topic]
            visible = text in (tr(title) + "\n" + tr(body)).casefold()
            item.setHidden(not visible)
            if visible and first is None:
                first = item
        self.empty.setVisible(first is None)
        current = self.topics.currentItem()
        if current is None or current.isHidden():
            self.topics.setCurrentItem(first)
            if first is None:
                self.browser.clear()

    def select_topic(self, topic):
        if topic not in TOPICS:
            topic = "overview"
        self.search.clear()
        for index in range(self.topics.count()):
            item = self.topics.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == topic:
                self.topics.setCurrentItem(item)
                self.topics.scrollToItem(item)
                return


def show_help(parent, topic="overview"):
    owner = parent.window() if parent is not None else None
    dialog = getattr(owner, "_dialpad_help_dialog", None) if owner is not None else None
    if dialog is None:
        dialog = HelpDialog(owner)
        if owner is not None:
            owner._dialpad_help_dialog = dialog
    dialog.select_topic(topic)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog


def attach_help(control, topic):
    title, body = TOPICS[topic]
    control.setProperty("helpTopic", topic)
    control.setWhatsThis(tr(body))
    if not control.toolTip():
        control.setToolTip(tr(title) + "\n" + tr("help.window.shortcut_hint"))


def help_button(topic, parent=None):
    title, _body = TOPICS[topic]
    button = QToolButton(parent)
    button.setObjectName("help_" + topic)
    button.setText("?")
    button.setAccessibleName(tr("help.window.topic_accessible", topic=tr(title)))
    button.setToolTip(tr("help.window.topic_accessible", topic=tr(title)))
    button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    button.setProperty("helpTopic", topic)
    button.clicked.connect(lambda: show_help(button, topic))
    return button


def with_help(control, topic, parent=None):
    container = QWidget(parent)
    row = QHBoxLayout(container)
    row.setContentsMargins(0, 0, 0, 0)
    row.addWidget(control, 1)
    row.addWidget(help_button(topic, container))
    attach_help(control, topic)
    return container


def show_context_help(window, default_topic="overview"):
    widget = QApplication.focusWidget()
    while widget is not None:
        topic = widget.property("helpTopic")
        if topic:
            return show_help(window, topic)
        widget = widget.parentWidget()
    return show_help(window, default_topic)


def add_help_toolbar(window):
    toolbar = QWidget(window)
    layout = QHBoxLayout(toolbar)
    layout.setContentsMargins(0, 0, 0, 0)
    help_open = QPushButton(tr("help.window.open"), toolbar)
    help_open.setObjectName("openManagerHelp")
    help_open.clicked.connect(lambda: show_help(window))
    layout.addWidget(help_open)
    layout.addStretch()
    label = QLabel(tr("common.language.label"), toolbar)
    layout.addWidget(label)
    languages = QComboBox(toolbar)
    languages.setObjectName("interfaceLanguage")
    label.setBuddy(languages)
    languages.addItem(tr("common.language.system"), "system")
    languages.addItem(tr("common.language.names.en_us"), "en_US")
    languages.addItem(tr("common.language.names.zh_cn"), "zh_CN")
    languages.addItem(tr("common.language.names.zh_tw"), "zh_TW")
    languages.setCurrentIndex(languages.findData(language_preference()))
    layout.addWidget(with_help(languages, "language", toolbar))
    note = QLabel(tr("common.language.restart_hint"), toolbar)
    note.setObjectName("languageRestartHint")
    note.setWordWrap(True)
    layout.addWidget(note)

    def changed():
        try:
            save_language_preference(languages.currentData())
        except (OSError, ValueError) as exc:
            languages.blockSignals(True)
            languages.setCurrentIndex(languages.findData(language_preference()))
            languages.blockSignals(False)
            QMessageBox.warning(window, tr("common.language.save_failed"), str(exc))
            return
        note.setText(tr("common.language.saved_hint"))

    languages.currentIndexChanged.connect(changed)
    action = QAction(tr("help.window.action"), window)
    action.setShortcut(QKeySequence("F1"))
    action.triggered.connect(lambda: show_context_help(window))
    window.addAction(action)
    return toolbar
