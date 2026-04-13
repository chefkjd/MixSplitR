import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from mixsplitr_ui_cards import GlassPage
from mixsplitr_ui_effects import SmoothScrollArea
from mixsplitr_ui_inputs import _NoScrollComboBox, _NoScrollDoubleSpinBox, _NoScrollSpinBox
from mixsplitr_devtools import annotate_widget, copy_widget_owner_source


def install_settings_ui_methods(app_cls):
    methods = (
        create_settings_page,
        _configure_settings_form_layout,
        _refresh_widget_style,
        _apply_inline_button_cell_style,
        _apply_inline_status_cell_style,
        _apply_settings_value_capsule_style,
        _apply_clickable_combo_style,
        _is_on_settings_page,
        _force_settings_capsule_field,
        _apply_history_action_cell_style,
        _apply_settings_field_bubble,
        _apply_settings_form_bubbles,
    )
    for method in methods:
        setattr(app_cls, method.__name__, method)


def create_settings_page(self):
    scroll = SmoothScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setStyleSheet("border: none; background-color: transparent;")
    scroll.setProperty("scrollbarSurfaceColor", "#1D2127")
    scroll.verticalScrollBar().setSingleStep(50)
    scroll.verticalScrollBar().setProperty("scrollbarSurfaceColor", "#1D2127")
    scroll.horizontalScrollBar().setProperty("scrollbarSurfaceColor", "#1D2127")
    vp = scroll.viewport()
    vp.setAutoFillBackground(False)
    vp.setStyleSheet("background-color: transparent;")
    self._configure_scroll_top_fade_mask(scroll)
    scroll.verticalScrollBar().valueChanged.connect(
        lambda _v, s=scroll: self._schedule_card_fade_for_scroll(s)
    )
    scroll.horizontalScrollBar().valueChanged.connect(
        lambda _v, s=scroll: self._schedule_card_fade_for_scroll(s)
    )

    page = GlassPage()
    annotate_widget(page, role="Settings Page")
    page.setProperty("settingsPage", "true")
    layout = QVBoxLayout(page)
    layout.setContentsMargins(28, 28, 28, 28)
    layout.setSpacing(20)
    self._track_layout_metrics(layout, base_margins=(28, 28, 28, 28), base_spacing=20)

    header_container = QWidget()
    header_row = QHBoxLayout(header_container)
    if sys.platform == "win32":
        header_row.setContentsMargins(0, 10, 0, 2)
    elif sys.platform == "darwin":
        header_row.setContentsMargins(0, 14, 0, 2)
    else:
        header_row.setContentsMargins(0, 0, 0, 0)
    header_row.setSpacing(0)
    header = QLabel("Settings")
    header.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    self._track_scalable(header, 28, "font-weight: 400;")
    header_row.addWidget(header, 0, Qt.AlignLeft | Qt.AlignVCenter)
    header_row.addStretch(1)
    if sys.platform == "win32":
        header_container.setMinimumHeight(62)
    elif sys.platform == "darwin":
        header_container.setMinimumHeight(66)
    else:
        self._track_min_height(header_container, 52)
    layout.addWidget(header_container)
    if self.top_card_dragstrip_buffer > 0:
        layout.addSpacing(self.top_card_dragstrip_buffer)

    mode_card = self.create_base_card(
        "Identification Settings",
        "Adjust fingerprinting and metadata behavior used across Splitter and Identifier.",
    )
    form_layout = QFormLayout()
    self._configure_settings_form_layout(form_layout)

    self.fingerprint_sample_spin = _NoScrollSpinBox()
    self.fingerprint_sample_spin.setRange(8, 45)
    self._force_settings_capsule_field(self.fingerprint_sample_spin, min_width=220)
    form_layout.addRow("Fingerprint Sample (sec):", self.fingerprint_sample_spin)

    self.fingerprint_probe_combo = _NoScrollComboBox()
    self.fingerprint_probe_combo.addItems(["Single (Center)", "Multi-Point (3)"])
    self._force_settings_capsule_field(self.fingerprint_probe_combo, min_width=220)
    form_layout.addRow("Probe Mode (ACRCloud):", self.fingerprint_probe_combo)

    self.shazam_enabled_check = QCheckBox("Enable Shazam")
    form_layout.addRow("Shazam:", self.shazam_enabled_check)

    self.show_id_source_check = QCheckBox("Show ID source metadata")
    form_layout.addRow("ID Source View:", self.show_id_source_check)

    self.probe_hint_label = QLabel("")
    self.probe_hint_label.setStyleSheet("color: #C4C7C5; font-size: 13px;")
    self.probe_hint_label.setWordWrap(True)
    self.probe_hint_label.setVisible(False)

    self._apply_settings_form_bubbles(form_layout)
    mode_card.layout().addLayout(form_layout)
    mode_card.layout().addWidget(self.probe_hint_label)
    layout.addWidget(mode_card)

    api_card = self.create_base_card("API Key Settings", "Manage and confirm APIs are valid.")
    api_form = QFormLayout()
    self._configure_settings_form_layout(api_form)

    self.acr_host_input = QLineEdit()
    self._force_settings_capsule_field(self.acr_host_input, min_width=360)
    api_form.addRow("ACR Host:", self.acr_host_input)

    self.acr_access_key_input = QLineEdit()
    self._force_settings_capsule_field(self.acr_access_key_input, min_width=360)
    api_form.addRow("ACR Access Key:", self.acr_access_key_input)

    self.acr_secret_input = QLineEdit()
    self.acr_secret_input.setEchoMode(QLineEdit.Password)
    self._force_settings_capsule_field(self.acr_secret_input, min_width=360)
    api_form.addRow("ACR Secret Key:", self.acr_secret_input)

    self.acoustid_key_input = QLineEdit()
    self._force_settings_capsule_field(self.acoustid_key_input, min_width=360)
    api_form.addRow("AcoustID API Key:", self.acoustid_key_input)

    self.lastfm_key_input = QLineEdit()
    self._force_settings_capsule_field(self.lastfm_key_input, min_width=360)
    api_form.addRow("Last.fm API Key:", self.lastfm_key_input)

    self._apply_settings_form_bubbles(api_form)
    api_card.layout().addLayout(api_form)
    api_test_row = QHBoxLayout()
    self.acr_api_status_label = QLabel("ACRCloud: Missing")
    self.acoustid_api_status_label = QLabel("AcoustID: Missing")
    self.lastfm_api_status_label = QLabel("Last.fm: Missing")
    self._apply_inline_status_cell_style(self.acr_api_status_label, state="missing")
    self._apply_inline_status_cell_style(self.acoustid_api_status_label, state="missing")
    self._apply_inline_status_cell_style(self.lastfm_api_status_label, state="missing")
    api_test_row.addWidget(self.acr_api_status_label)
    api_test_row.addWidget(self.acoustid_api_status_label)
    api_test_row.addWidget(self.lastfm_api_status_label)
    api_test_row.addStretch()
    api_card.layout().addLayout(api_test_row)
    self.api_test_status_label = QLabel("API Test: Ready")
    self.api_test_status_label.setStyleSheet("color: #C4C7C5;")
    self.api_test_status_label.setWordWrap(True)
    api_card.layout().addWidget(self.api_test_status_label)
    layout.addWidget(api_card)

    directory_card = self.create_base_card(
        "Directory Settings",
        "Output, timestamping, recording, temp workspace, history, and CD-rip folders.",
    )
    dir_form = QFormLayout()
    self._configure_settings_form_layout(dir_form)

    self.output_dir_input = QLineEdit()
    self._force_settings_capsule_field(self.output_dir_input, min_width=360)
    out_row = QHBoxLayout()
    out_btn = QPushButton("Browse")
    out_btn.setProperty("class", "SecondaryButton")
    out_btn.setProperty("settingsInlineCell", "true")
    self._apply_inline_button_cell_style(out_btn)
    self._refresh_widget_style(out_btn)
    out_btn.clicked.connect(lambda: self._browse_directory_into(self.output_dir_input))
    out_row.addWidget(self.output_dir_input)
    out_row.addWidget(out_btn)
    dir_form.addRow("Output Folder:", out_row)

    self.timestamp_output_dir_input = QLineEdit()
    self._force_settings_capsule_field(self.timestamp_output_dir_input, min_width=360)
    self.timestamp_output_dir_input.setPlaceholderText("Uses Output Folder/Tracklists when empty")
    self.timestamp_output_dir_input.setToolTip(
        "Optional override for timestamping exports.\n"
        "When left blank, timestamp files are written to Output Folder/Tracklists."
    )
    timestamp_out_row = QHBoxLayout()
    timestamp_out_btn = QPushButton("Browse")
    timestamp_out_btn.setProperty("class", "SecondaryButton")
    timestamp_out_btn.setProperty("settingsInlineCell", "true")
    self._apply_inline_button_cell_style(timestamp_out_btn)
    self._refresh_widget_style(timestamp_out_btn)
    timestamp_out_btn.clicked.connect(lambda: self._browse_directory_into(self.timestamp_output_dir_input))
    timestamp_out_row.addWidget(self.timestamp_output_dir_input)
    timestamp_out_row.addWidget(timestamp_out_btn)
    dir_form.addRow("Timestamp Output Folder:", timestamp_out_row)

    self.output_format_combo = _NoScrollComboBox()
    for key, label in self._audio_output_format_options():
        self.output_format_combo.addItem(label, key)
    self._force_settings_capsule_field(self.output_format_combo, min_width=220)
    dir_form.addRow("Output Audio Format:", self.output_format_combo)
    self.rename_preset_combo = _NoScrollComboBox()
    for key, label in self._renaming_preset_options():
        self.rename_preset_combo.addItem(label, key)
    self._force_settings_capsule_field(self.rename_preset_combo, min_width=360)
    self.rename_preset_combo.setToolTip(
        "Choose a naming preset for exported files.\n"
        "Simple: Artist - Title\n"
        "Album Folders: Artist/Album/Track - Title\n"
        "Discography: Artist/Year/Album/Track - Title\n"
        "Podcast: Artist/Title"
    )
    dir_form.addRow("Rename + Folder Preset:", self.rename_preset_combo)
    self.preserve_source_format_check = QCheckBox(
        "Keep source format for passthrough tracks (metadata-only tagging)"
    )
    self.preserve_source_format_check.setToolTip(
        "When enabled, tracks processed as single passthrough files are copied in their original format\n"
        "and tagged without re-encoding. Split mix chunks still export normally."
    )
    dir_form.addRow("Preserve Source Format:", self.preserve_source_format_check)

    self.recording_force_wav_check = QCheckBox(
        "Force WAV recording (disable FLAC in Recording Mode, limits file size to 4gb which limits recording time)"
    )
    self.recording_force_wav_check.setToolTip(
        "When enabled, Recording Mode writes WAV files instead of FLAC.\n"
        "On Windows WASAPI loopback, WAV recording auto-splits into parts before file-size limits."
    )
    dir_form.addRow("Recording Format Override:", self.recording_force_wav_check)

    self.recording_dir_input = QLineEdit()
    self._force_settings_capsule_field(self.recording_dir_input, min_width=360)
    rec_row = QHBoxLayout()
    rec_btn = QPushButton("Browse")
    rec_btn.setProperty("class", "SecondaryButton")
    rec_btn.setProperty("settingsInlineCell", "true")
    self._apply_inline_button_cell_style(rec_btn)
    self._refresh_widget_style(rec_btn)
    rec_btn.clicked.connect(lambda: self._browse_directory_into(self.recording_dir_input))
    rec_row.addWidget(self.recording_dir_input)
    rec_row.addWidget(rec_btn)
    dir_form.addRow("Recording Folder:", rec_row)

    self.temp_workspace_dir_input = QLineEdit()
    self._force_settings_capsule_field(self.temp_workspace_dir_input, min_width=360)
    temp_row = QHBoxLayout()
    temp_btn = QPushButton("Browse")
    temp_btn.setProperty("class", "SecondaryButton")
    temp_btn.setProperty("settingsInlineCell", "true")
    self._apply_inline_button_cell_style(temp_btn)
    self._refresh_widget_style(temp_btn)
    temp_btn.clicked.connect(lambda: self._browse_directory_into(self.temp_workspace_dir_input))
    temp_row.addWidget(self.temp_workspace_dir_input)
    temp_row.addWidget(temp_btn)
    dir_form.addRow("Temp Workspace Folder:", temp_row)
    self.temp_workspace_dir_input.setToolTip(
        "Location for transient split/fingerprint/preview temp files.\n"
        "Use a drive with enough free space for large jobs."
    )

    self.manifest_dir_input = QLineEdit()
    self._force_settings_capsule_field(self.manifest_dir_input, min_width=360)
    man_row = QHBoxLayout()
    man_btn = QPushButton("Browse")
    man_btn.setProperty("class", "SecondaryButton")
    man_btn.setProperty("settingsInlineCell", "true")
    self._apply_inline_button_cell_style(man_btn)
    self._refresh_widget_style(man_btn)
    man_btn.clicked.connect(lambda: self._browse_directory_into(self.manifest_dir_input))
    man_row.addWidget(self.manifest_dir_input)
    man_row.addWidget(man_btn)
    dir_form.addRow("Session History Folder:", man_row)

    self.cd_output_dir_input = QLineEdit()
    self._force_settings_capsule_field(self.cd_output_dir_input, min_width=360)
    cd_out_row = QHBoxLayout()
    cd_out_btn = QPushButton("Browse")
    cd_out_btn.setProperty("class", "SecondaryButton")
    cd_out_btn.setProperty("settingsInlineCell", "true")
    self._apply_inline_button_cell_style(cd_out_btn)
    self._refresh_widget_style(cd_out_btn)
    cd_out_btn.clicked.connect(lambda: self._browse_directory_into(self.cd_output_dir_input))
    cd_out_row.addWidget(self.cd_output_dir_input)
    cd_out_row.addWidget(cd_out_btn)
    dir_form.addRow("CD Rip Output Folder:", cd_out_row)

    self._apply_settings_form_bubbles(dir_form)
    directory_card.layout().addLayout(dir_form)
    layout.addWidget(directory_card)

    essentia_simple_card = self.create_base_card(
        "Essentia (Simple)",
        "Quick controls for timestamp assist and genre help. Enable advanced controls for full tuning.",
    )
    essentia_simple_form = QFormLayout()
    self._configure_settings_form_layout(essentia_simple_form)

    self.essentia_simple_assist_check = QCheckBox("Enable Essentia transition assist")
    essentia_simple_form.addRow("Essentia Assist:", self.essentia_simple_assist_check)

    self.essentia_simple_strength_combo = _NoScrollComboBox()
    self.essentia_simple_strength_combo.setProperty("settingsValueCapsule", "true")
    self.essentia_simple_strength_combo.setMinimumWidth(220)
    self._apply_settings_value_capsule_style(self.essentia_simple_strength_combo)
    self.essentia_simple_strength_combo.addItem(
        self.ESSENTIA_SIMPLE_PROFILE_LABELS["conservative"],
        "conservative",
    )
    self.essentia_simple_strength_combo.addItem(
        self.ESSENTIA_SIMPLE_PROFILE_LABELS["balanced"],
        "balanced",
    )
    self.essentia_simple_strength_combo.addItem(
        self.ESSENTIA_SIMPLE_PROFILE_LABELS["aggressive"],
        "aggressive",
    )
    self.essentia_simple_strength_combo.addItem(
        self.ESSENTIA_SIMPLE_PROFILE_LABELS["custom"],
        "custom",
    )
    self.essentia_simple_strength_combo.setToolTip(
        "Balanced mirrors current defaults.\n"
        "Custom appears when advanced values do not match a preset."
    )
    essentia_simple_form.addRow("Assist Strength:", self.essentia_simple_strength_combo)

    self.essentia_simple_genre_help_check = QCheckBox("Enable Essentia genre enrichment")
    essentia_simple_form.addRow("Genre Help:", self.essentia_simple_genre_help_check)

    self.essentia_simple_when_missing_check = QCheckBox("Only fill missing genres")
    essentia_simple_form.addRow("Only Fill Missing:", self.essentia_simple_when_missing_check)

    self.essentia_show_advanced_check = QCheckBox("Show Advanced Essentia Controls")
    essentia_simple_form.addRow("Advanced:", self.essentia_show_advanced_check)

    self._apply_settings_form_bubbles(essentia_simple_form)
    essentia_simple_card.layout().addLayout(essentia_simple_form)
    layout.addWidget(essentia_simple_card)

    timestamp_card = self.create_base_card(
        "Advanced Timestamping",
        "Fine-tune timestamp scanning and the transition detector used for Continuous Mix splitting.",
    )
    timestamp_form = QFormLayout()
    self._configure_settings_form_layout(timestamp_form)

    self.transition_detection_profile_combo = _NoScrollComboBox()
    self.transition_detection_profile_combo.setProperty("settingsValueCapsule", "true")
    self.transition_detection_profile_combo.setMinimumWidth(220)
    self._apply_settings_value_capsule_style(self.transition_detection_profile_combo)
    self.transition_detection_profile_combo.addItem(
        self.TRANSITION_DETECTION_PROFILE_LABELS["balanced"],
        "balanced",
    )
    self.transition_detection_profile_combo.addItem(
        self.TRANSITION_DETECTION_PROFILE_LABELS["electronic"],
        "electronic",
    )
    self.transition_detection_profile_combo.addItem(
        self.TRANSITION_DETECTION_PROFILE_LABELS["rock"],
        "rock",
    )
    self.transition_detection_profile_combo.addItem(
        self.TRANSITION_DETECTION_PROFILE_LABELS["custom"],
        "custom",
    )
    self.transition_detection_profile_combo.setToolTip(
        "Balanced mirrors current defaults.\n"
        "Electronic / DJ Mix keeps more transition candidates for blended sets.\n"
        "Rock / Band Mix is stricter to reduce false cuts from fills and breakdowns.\n"
        "Custom appears when the advanced values do not match a preset."
    )
    timestamp_form.addRow("Transition Preset:", self.transition_detection_profile_combo)

    self.window_spin = _NoScrollSpinBox()
    self.window_spin.setRange(8, 60)
    self._force_settings_capsule_field(self.window_spin, min_width=220)
    timestamp_form.addRow("Window Scan Size (sec):", self.window_spin)

    self.step_spin = _NoScrollSpinBox()
    self.step_spin.setRange(5, 120)
    self._force_settings_capsule_field(self.step_spin, min_width=220)
    timestamp_form.addRow("Step Size (sec):", self.step_spin)

    self.min_segment_spin = _NoScrollSpinBox()
    self.min_segment_spin.setRange(15, 300)
    self._force_settings_capsule_field(self.min_segment_spin, min_width=220)
    timestamp_form.addRow("Minimum Segment (sec):", self.min_segment_spin)

    self.fallback_interval_spin = _NoScrollSpinBox()
    self.fallback_interval_spin.setRange(60, 900)
    self._force_settings_capsule_field(self.fallback_interval_spin, min_width=220)
    timestamp_form.addRow("Fallback Interval (sec):", self.fallback_interval_spin)

    self.max_windows_spin = _NoScrollSpinBox()
    self.max_windows_spin.setRange(20, 1000)
    self._force_settings_capsule_field(self.max_windows_spin, min_width=220)
    timestamp_form.addRow("Max Windows:", self.max_windows_spin)

    self.conf_spin = _NoScrollDoubleSpinBox()
    self.conf_spin.setRange(0.25, 0.95)
    self.conf_spin.setSingleStep(0.01)
    self._force_settings_capsule_field(self.conf_spin, min_width=220)
    timestamp_form.addRow("Minimum Confidence:", self.conf_spin)

    self.boundary_backtrack_spin = _NoScrollDoubleSpinBox()
    self.boundary_backtrack_spin.setRange(0.0, 20.0)
    self.boundary_backtrack_spin.setSingleStep(0.1)
    self._force_settings_capsule_field(self.boundary_backtrack_spin, min_width=220)
    timestamp_form.addRow("Boundary Backtrack (sec):", self.boundary_backtrack_spin)

    self.auto_no_identify_check = QCheckBox("Enable Track NN labels only")
    timestamp_form.addRow("No-ID Labels:", self.auto_no_identify_check)

    self.auto_tracklist_essentia_enabled_check = QCheckBox("Enable Essentia transition assist")
    timestamp_form.addRow("Essentia Assist:", self.auto_tracklist_essentia_enabled_check)

    self.auto_tracklist_essentia_conf_spin = _NoScrollDoubleSpinBox()
    self.auto_tracklist_essentia_conf_spin.setRange(0.05, 0.95)
    self.auto_tracklist_essentia_conf_spin.setSingleStep(0.01)
    self._force_settings_capsule_field(self.auto_tracklist_essentia_conf_spin, min_width=220)
    timestamp_form.addRow("Essentia Min Confidence:", self.auto_tracklist_essentia_conf_spin)

    self.auto_tracklist_essentia_max_points_spin = _NoScrollSpinBox()
    self.auto_tracklist_essentia_max_points_spin.setRange(200, 6000)
    self._force_settings_capsule_field(self.auto_tracklist_essentia_max_points_spin, min_width=220)
    timestamp_form.addRow("Essentia Max Points:", self.auto_tracklist_essentia_max_points_spin)

    self.timestamp_hint_label = QLabel("")
    self.timestamp_hint_label.setStyleSheet("color: #C4C7C5; font-size: 13px;")
    self.timestamp_hint_label.setWordWrap(True)

    self._apply_settings_form_bubbles(timestamp_form)
    timestamp_card.layout().addLayout(timestamp_form)
    timestamp_card.layout().addWidget(self.timestamp_hint_label)
    layout.addWidget(timestamp_card)

    essentia_card = self.create_base_card(
        "Genre Enrichment",
        "Toggle genre enrichment features and control their behavior.",
    )
    self.essentia_advanced_card = essentia_card
    ess_layout = QFormLayout()
    self._configure_settings_form_layout(ess_layout)

    self.essentia_conf = _NoScrollDoubleSpinBox()
    self.essentia_conf.setRange(0.2, 0.9)
    self.essentia_conf.setSingleStep(0.01)
    self._force_settings_capsule_field(self.essentia_conf, min_width=220)
    ess_layout.addRow("Minimum Confidence:", self.essentia_conf)

    self.essentia_enabled_check = QCheckBox("Enable Essentia genre enrichment")
    ess_layout.addRow("Enabled:", self.essentia_enabled_check)

    self.essentia_when_missing_check = QCheckBox("Only apply when genre metadata is missing")
    ess_layout.addRow("When Missing Only:", self.essentia_when_missing_check)

    self.essentia_max_tags_spin = _NoScrollSpinBox()
    self.essentia_max_tags_spin.setRange(1, 5)
    self._force_settings_capsule_field(self.essentia_max_tags_spin, min_width=220)
    ess_layout.addRow("Maximum Genre Tags:", self.essentia_max_tags_spin)

    self.essentia_analysis_seconds_spin = _NoScrollSpinBox()
    self.essentia_analysis_seconds_spin.setRange(8, 60)
    self._force_settings_capsule_field(self.essentia_analysis_seconds_spin, min_width=220)
    ess_layout.addRow("Analysis Length (sec):", self.essentia_analysis_seconds_spin)

    self.essentia_runtime_status_value = QLabel("Checking Essentia runtime...")
    self.essentia_runtime_status_value.setProperty("settingsValueCapsule", "true")
    self.essentia_runtime_status_value.setWordWrap(True)
    self._apply_settings_value_capsule_style(self.essentia_runtime_status_value)
    ess_layout.addRow("Runtime Status:", self.essentia_runtime_status_value)

    self.lastfm_genre_check = QCheckBox("Use Last.fm tags for genre enrichment")
    self.lastfm_genre_check.setToolTip(
        "When enabled, Last.fm's crowd-sourced track tags (e.g. 'drum and bass', 'dream pop') "
        "are merged into the genre field alongside iTunes and Deezer data. "
        "Particularly useful for niche electronic subgenres that streaming catalogues often mislabel or omit. "
        "Has no effect if no Last.fm API key is configured."
    )
    ess_layout.addRow("Last.fm Genres:", self.lastfm_genre_check)

    self._apply_settings_form_bubbles(ess_layout)
    essentia_card.layout().addLayout(ess_layout)
    layout.addWidget(essentia_card)
    self._essentia_advanced_widgets = [
        self.auto_tracklist_essentia_enabled_check,
        self.auto_tracklist_essentia_conf_spin,
        self.auto_tracklist_essentia_max_points_spin,
    ]
    self._set_essentia_advanced_controls_visible(False)

    behavior_card = self.create_base_card(
        "General Behavior",
        "Processing toggles and recording options.",
    )
    behavior_layout = QFormLayout()
    self._configure_settings_form_layout(behavior_layout)

    self.enable_album_search_check = QCheckBox("Enable album search/grouping")
    behavior_layout.addRow("Album Search:", self.enable_album_search_check)

    self.artist_normalization_mode_combo = _NoScrollComboBox()
    self.artist_normalization_mode_combo.setProperty("settingsValueCapsule", "true")
    self.artist_normalization_mode_combo.setMinimumWidth(220)
    self._apply_settings_value_capsule_style(self.artist_normalization_mode_combo)
    for mode_key, mode_label in self._artist_normalization_mode_options():
        self.artist_normalization_mode_combo.addItem(mode_label, mode_key)
    self.artist_normalization_mode_combo.setToolTip(
        "Off: no artist folder normalization.\n"
        "Collab Only: split feat/& credits to group collabs under one primary folder.\n"
        "Smart: scored canonicalization (aliases/fuzzy/MB corroboration) for folder routing."
    )
    behavior_layout.addRow("Artist Normalization:", self.artist_normalization_mode_combo)

    self.artist_normalization_strictness_spin = _NoScrollDoubleSpinBox()
    self.artist_normalization_strictness_spin.setRange(0.75, 0.99)
    self.artist_normalization_strictness_spin.setSingleStep(0.01)
    self._force_settings_capsule_field(self.artist_normalization_strictness_spin, min_width=220)
    self.artist_normalization_strictness_spin.setToolTip(
        "Smart-mode merge threshold. Higher values reduce auto-merges."
    )
    behavior_layout.addRow("Smart Merge Strictness:", self.artist_normalization_strictness_spin)

    self.artist_normalization_collapse_backing_band_check = QCheckBox(
        "Collapse 'Artist and the Band' variants to solo artist"
    )
    behavior_layout.addRow(
        "Collapse Backing Band:",
        self.artist_normalization_collapse_backing_band_check,
    )

    self.artist_normalization_review_ambiguous_check = QCheckBox(
        "Review ambiguous merges before applying"
    )
    behavior_layout.addRow(
        "Review Ambiguous Merges:",
        self.artist_normalization_review_ambiguous_check,
    )

    self.deep_scan_check = QCheckBox(
        "Recursively scan subfolders when a folder is loaded in Audio Splitter"
    )
    behavior_layout.addRow("Folder Deep Scan:", self.deep_scan_check)

    self.local_bpm_check = QCheckBox("Enable local BPM detection")
    behavior_layout.addRow("Local BPM:", self.local_bpm_check)

    self.split_sensitivity_value = self._normalize_split_sensitivity_value(0)
    split_sens_widget = QWidget()
    split_sens_row = QHBoxLayout(split_sens_widget)
    split_sens_row.setContentsMargins(0, 0, 0, 0)
    split_sens_row.setSpacing(8)

    self.split_sensitivity_left_btn = QPushButton("<")
    self.split_sensitivity_left_btn.setProperty("class", "SecondaryButton")
    self.split_sensitivity_left_btn.setProperty("settingsInlineCell", "true")
    self.split_sensitivity_left_btn.setFixedWidth(34)
    self._apply_inline_button_cell_style(self.split_sensitivity_left_btn)
    self._refresh_widget_style(self.split_sensitivity_left_btn)
    self.split_sensitivity_left_btn.clicked.connect(lambda: self._adjust_split_sensitivity_offset(-1))

    self.split_sensitivity_value_label = QLabel("")
    self.split_sensitivity_value_label.setAlignment(Qt.AlignCenter)
    self.split_sensitivity_value_label.setMinimumHeight(34)
    self.split_sensitivity_value_label.setMinimumWidth(220)
    self.split_sensitivity_value_label.setProperty("settingsEditableCellLabel", "true")
    self.split_sensitivity_value_label.setProperty("settingsValueCapsule", "true")
    self._apply_settings_value_capsule_style(self.split_sensitivity_value_label)
    self._refresh_widget_style(self.split_sensitivity_value_label)
    self.split_sensitivity_value_label.setToolTip(
        "Offset from base split threshold (-38 dBFS).\n"
        "Increase for higher split sensitivity, reduce for lower."
    )

    self.split_sensitivity_right_btn = QPushButton(">")
    self.split_sensitivity_right_btn.setProperty("class", "SecondaryButton")
    self.split_sensitivity_right_btn.setProperty("settingsInlineCell", "true")
    self.split_sensitivity_right_btn.setFixedWidth(34)
    self._apply_inline_button_cell_style(self.split_sensitivity_right_btn)
    self._refresh_widget_style(self.split_sensitivity_right_btn)
    self.split_sensitivity_right_btn.clicked.connect(lambda: self._adjust_split_sensitivity_offset(1))

    split_sens_row.addWidget(self.split_sensitivity_left_btn)
    split_sens_row.addWidget(self.split_sensitivity_value_label, 1)
    split_sens_row.addWidget(self.split_sensitivity_right_btn)
    self._refresh_split_sensitivity_controls()
    behavior_layout.addRow("Split Sensitivity Offset:", split_sens_widget)

    self.split_seek_step_combo = _NoScrollComboBox()
    self.split_seek_step_combo.setProperty("settingsValueCapsule", "true")
    self.split_seek_step_combo.setMinimumWidth(180)
    self._apply_settings_value_capsule_style(self.split_seek_step_combo)
    for option_ms in self._split_seek_step_options():
        self.split_seek_step_combo.addItem(f"{int(option_ms)} ms", int(option_ms))
    self.split_seek_step_combo.setToolTip(
        "Silence detection scan interval.\n"
        "10 ms = highest precision, 30 ms = fastest scan."
    )
    behavior_layout.addRow("Split Seek Step:", self.split_seek_step_combo)

    self.duplicate_policy_combo = _NoScrollComboBox()
    self.duplicate_policy_combo.setProperty("settingsValueCapsule", "true")
    self.duplicate_policy_combo.setMinimumWidth(220)
    self._apply_settings_value_capsule_style(self.duplicate_policy_combo)
    for policy_key, policy_label in self._duplicate_policy_options():
        self.duplicate_policy_combo.addItem(policy_label, policy_key)
    self.duplicate_policy_combo.setToolTip(
        "How to handle collisions when an output filename already exists.\n"
        "Skip keeps files untouched, Overwrite replaces, Keep Highest Quality keeps the best duplicate across formats."
    )
    behavior_layout.addRow("Duplicate Handling:", self.duplicate_policy_combo)

    self.long_track_prompt_check = QCheckBox("Prompt before splitting long files")
    behavior_layout.addRow("Long Track Prompt:", self.long_track_prompt_check)

    self.long_track_threshold_spin = _NoScrollDoubleSpinBox()
    self.long_track_threshold_spin.setProperty("settingsValueCapsule", "true")
    self.long_track_threshold_spin.setMinimumWidth(180)
    self._apply_settings_value_capsule_style(self.long_track_threshold_spin)
    self.long_track_threshold_spin.setRange(1.0, 60.0)
    self.long_track_threshold_spin.setSingleStep(0.5)
    self.long_track_threshold_spin.setSuffix(" min")
    self.long_track_threshold_spin.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    threshold_line_edit = self.long_track_threshold_spin.lineEdit()
    if threshold_line_edit is not None:
        threshold_line_edit.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    behavior_layout.addRow("Long Track Threshold:", self.long_track_threshold_spin)

    self.recording_silence_timeout_spin = _NoScrollDoubleSpinBox()
    self.recording_silence_timeout_spin.setProperty("settingsValueCapsule", "true")
    self.recording_silence_timeout_spin.setMinimumWidth(180)
    self._apply_settings_value_capsule_style(self.recording_silence_timeout_spin)
    self.recording_silence_timeout_spin.setRange(2.0, 120.0)
    self.recording_silence_timeout_spin.setSingleStep(1.0)
    self.recording_silence_timeout_spin.setSuffix(" s")
    self.recording_silence_timeout_spin.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    timeout_line_edit = self.recording_silence_timeout_spin.lineEdit()
    if timeout_line_edit is not None:
        timeout_line_edit.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    behavior_layout.addRow("Recording Silence Auto-Stop:", self.recording_silence_timeout_spin)

    self.recording_keep_screen_awake_check = QCheckBox(
        "Prevent display sleep during active recording sessions"
    )
    self.recording_keep_screen_awake_check.setToolTip(
        "Keeps the screen awake while Recording Mode is actively capturing audio.\n"
        "Supported on Windows and macOS. Automatically releases when recording stops."
    )
    behavior_layout.addRow("Keep Screen Awake:", self.recording_keep_screen_awake_check)

    self._apply_settings_form_bubbles(behavior_layout)
    behavior_card.layout().addLayout(behavior_layout)
    layout.insertWidget(1 + int(self.top_card_dragstrip_buffer > 0), behavior_card)
    self._update_artist_normalization_ui_state()

    advanced_toggles_card = self.create_base_card(
        "Advanced Toggles",
        "Debugging and troubleshooting switches for power users.",
    )
    advanced_toggles_layout = QFormLayout()
    self._configure_settings_form_layout(advanced_toggles_layout)

    self.debug_readout_check = QCheckBox("Enable debug readout window")
    advanced_toggles_layout.addRow("Debug Readout:", self.debug_readout_check)

    self.developer_inspector_check = QCheckBox("Enable live code-location inspector")
    advanced_toggles_layout.addRow("Developer Mode:", self.developer_inspector_check)

    self.debug_readout_open_btn = QPushButton("Open Debug Readout")
    self.debug_readout_open_btn.setProperty("class", "SecondaryButton")
    self.debug_readout_open_btn.setProperty("settingsInlineCell", "true")
    self._apply_inline_button_cell_style(self.debug_readout_open_btn)
    self._refresh_widget_style(self.debug_readout_open_btn)
    self.debug_readout_open_btn.clicked.connect(self._open_debug_readout_window)
    advanced_toggles_layout.addRow("", self.debug_readout_open_btn)

    self._apply_settings_form_bubbles(advanced_toggles_layout)
    advanced_toggles_card.layout().addLayout(advanced_toggles_layout)
    layout.addWidget(advanced_toggles_card)

    cd_card = self.create_base_card(
        "CD Ripping Options",
        "Choose output format and metadata behavior for ripped CDs.",
    )
    cd_layout = QFormLayout()
    self._configure_settings_form_layout(cd_layout)

    self.cd_format_combo = _NoScrollComboBox()
    self.cd_format_combo.addItems(["FLAC", "WAV", "MP3 320kbps"])
    self._force_settings_capsule_field(self.cd_format_combo, min_width=220)
    cd_layout.addRow("Rip Format:", self.cd_format_combo)

    self.cd_auto_metadata_check = QCheckBox("Enable CD auto metadata")
    cd_layout.addRow("Auto Metadata:", self.cd_auto_metadata_check)

    self.cd_eject_check = QCheckBox("Eject disc when done")
    cd_layout.addRow("Eject When Done:", self.cd_eject_check)

    self._apply_settings_form_bubbles(cd_layout)
    cd_card.layout().addLayout(cd_layout)
    layout.addWidget(cd_card)

    layout.addStretch()
    self._apply_card_line_surfaces_on_page(page)
    scroll.setWidget(page)
    QTimer.singleShot(0, lambda s=scroll: self._schedule_card_fade_for_scroll(s))
    return scroll


def _configure_settings_form_layout(self, form_layout):
    if form_layout is None:
        return
    form_layout.setContentsMargins(0, 0, 0, 0)
    form_layout.setVerticalSpacing(12)
    form_layout.setHorizontalSpacing(14)
    form_layout.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
    form_layout.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
    form_layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    form_layout.setRowWrapPolicy(QFormLayout.DontWrapRows)


def _refresh_widget_style(self, widget):
    if widget is None:
        return
    try:
        style = widget.style()
        if style is not None:
            style.unpolish(widget)
            style.polish(widget)
    except Exception:
        pass
    widget.update()


def _apply_inline_button_cell_style(self, button):
    if button is None:
        return
    button.setCursor(Qt.PointingHandCursor)
    button.setMinimumHeight(34)
    button.setStyleSheet(
        """
        QPushButton {
            background-color: #22262B;
            border: none;
            border-radius: 8px;
            padding: 8px 14px;
            color: #EBE7E1;
        }
        QPushButton:hover {
            background-color: #292E34;
        }
        QPushButton:pressed {
            background-color: #1F2328;
        }
        QPushButton:disabled {
            background-color: #1C1F23;
            border: none;
            color: #7F7A72;
        }
        """
    )


def _apply_inline_status_cell_style(self, label, state="neutral"):
    if label is None:
        return
    palette = {
        "neutral": {
            "bg": "#22262B",
            "border": "transparent",
            "color": "#EBE7E1",
        },
        "missing": {
            "bg": "#1F2328",
            "border": "transparent",
            "color": "#A7A196",
        },
        "checking": {
            "bg": "#2A2F36",
            "border": "transparent",
            "color": "#EBE7E1",
        },
        "valid": {
            "bg": "#26362A",
            "border": "transparent",
            "color": "#D8ECD9",
        },
        "invalid": {
            "bg": "#3A2428",
            "border": "transparent",
            "color": "#F1D7DB",
        },
    }
    style = palette.get(str(state or "neutral").strip().lower(), palette["neutral"])
    label.setAlignment(Qt.AlignCenter)
    label.setMinimumHeight(34)
    label.setStyleSheet(
        f"""
        QLabel {{
            background-color: {style['bg']};
            border: none;
            border-radius: 8px;
            padding: 8px 14px;
            color: {style['color']};
        }}
        """
    )


def _apply_settings_value_capsule_style(self, widget):
    if widget is None:
        return
    if isinstance(widget, QLineEdit):
        widget.setMinimumHeight(max(widget.minimumHeight(), 34))
        widget.setStyleSheet(
            """
            QLineEdit {
                background-color: #1F2328;
                border: none;
                border-radius: 8px;
                padding: 7px 12px;
                color: #EBE7E1;
            }
            QLineEdit:hover {
                background-color: #24292F;
            }
            QLineEdit:focus {
                background-color: #273449;
                border: none;
            }
            QLineEdit:disabled {
                background-color: #1A1D21;
                border: none;
                color: #7F7A72;
            }
            """
        )
        return
    if isinstance(widget, QTextEdit):
        widget.setStyleSheet(
            """
            QTextEdit {
                background-color: #1F2328;
                border: none;
                border-radius: 8px;
                padding: 10px 12px;
                color: #EBE7E1;
            }
            QTextEdit:hover {
                background-color: #24292F;
            }
            QTextEdit:focus {
                background-color: #273449;
                border: none;
            }
            QTextEdit:disabled {
                background-color: #1A1D21;
                border: none;
                color: #7F7A72;
            }
            """
        )
        return
    if isinstance(widget, QLabel):
        widget.setMinimumHeight(max(widget.minimumHeight(), 34))
        widget.setStyleSheet(
            """
            QLabel {
                background-color: #22262B;
                border: none;
                border-radius: 8px;
                padding: 7px 12px;
                color: #EBE7E1;
            }
            """
        )
        return
    if isinstance(widget, QComboBox):
        widget.setMinimumHeight(max(widget.minimumHeight(), 34))
        widget.setStyleSheet(
            """
            QComboBox {
                background-color: #1F2328;
                border: none;
                border-radius: 8px;
                padding: 7px 12px;
                color: #EBE7E1;
            }
            QComboBox:hover {
                background-color: #24292F;
            }
            QComboBox:focus {
                background-color: #273449;
                border: none;
            }
            QComboBox:disabled {
                background-color: #1A1D21;
                border: none;
                color: #7F7A72;
            }
            QComboBox::drop-down {
                border: none;
                width: 24px;
            }
            """
        )
        return
    if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
        widget.setMinimumHeight(max(widget.minimumHeight(), 34))
        widget.setStyleSheet(
            """
            QSpinBox, QDoubleSpinBox {
                background-color: #1F2328;
                border: none;
                border-radius: 8px;
                padding: 0px 12px;
                color: #EBE7E1;
            }
            QSpinBox:hover, QDoubleSpinBox:hover {
                background-color: #24292F;
            }
            QSpinBox:focus, QDoubleSpinBox:focus {
                background-color: #273449;
                border: none;
            }
            QSpinBox:disabled, QDoubleSpinBox:disabled {
                background-color: #1A1D21;
                border: none;
                color: #7F7A72;
            }
            QSpinBox::up-button, QSpinBox::down-button,
            QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
                width: 0px;
            }
            """
        )


def _apply_clickable_combo_style(self, widget):
    if widget is None:
        return
    widget.setMinimumHeight(max(widget.minimumHeight(), 46))
    widget.setCursor(Qt.PointingHandCursor)
    widget.setStyleSheet(
        """
        QComboBox {
            background-color: #1F2328;
            border: none;
            border-radius: 8px;
            padding: 10px 52px 10px 16px;
            color: #EBE7E1;
        }
        QComboBox:hover {
            background-color: #24292F;
        }
        QComboBox:focus,
        QComboBox:on {
            background-color: #273449;
            border: none;
        }
        QComboBox:disabled {
            background-color: #1A1D21;
            border: none;
            color: #7F7A72;
        }
        QComboBox::drop-down {
            subcontrol-origin: padding;
            subcontrol-position: top right;
            width: 42px;
            margin: 1px 1px 1px 0px;
            border-left: none;
            border-top-right-radius: 7px;
            border-bottom-right-radius: 7px;
            background-color: #24292F;
        }
        QComboBox::drop-down:hover {
            background-color: #292E34;
        }
        QComboBox::down-arrow {
            image: none;
            width: 0px;
            height: 0px;
        }
        """
    )


def _is_on_settings_page(self, widget):
    node = widget
    while node is not None:
        if str(node.property("settingsPage")).strip().lower() == "true":
            return True
        node = node.parentWidget()
    return False


def _force_settings_capsule_field(self, widget, min_width=220):
    if widget is None:
        return
    widget.setProperty("settingsBubbleField", "true")
    widget.setProperty("settingsEditableCell", "true")
    widget.setProperty("settingsValueCapsule", "true")
    if isinstance(widget, (QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit)):
        widget.setMinimumHeight(max(widget.minimumHeight(), 34))
        if isinstance(widget, (QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox)) and min_width:
            widget.setMinimumWidth(max(widget.minimumWidth(), int(min_width)))
            size_policy = widget.sizePolicy()
            widget.setSizePolicy(QSizePolicy.Expanding, size_policy.verticalPolicy())
        self._apply_settings_value_capsule_style(widget)
        if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
            widget.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            line_edit = widget.lineEdit()
            if line_edit is not None:
                line_edit.setFrame(False)
                line_edit.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    self._refresh_widget_style(widget)


def _apply_history_action_cell_style(self, button):
    if button is None:
        return
    button.setCursor(Qt.PointingHandCursor)
    button.setMinimumHeight(36)
    button.setStyleSheet(
        """
        QPushButton {
            background-color: #22262B;
            border: none;
            border-radius: 8px;
            padding: 8px 14px;
            color: #EBE7E1;
        }
        QPushButton:hover {
            background-color: #292E34;
        }
        QPushButton:pressed {
            background-color: #1F2328;
        }
        QPushButton:disabled {
            background-color: #1C1F23;
            border: none;
            color: #7F7A72;
        }
        """
    )


def _apply_settings_field_bubble(self, widget, label_widget=None):
    if widget is None:
        return
    on_settings_page = self._is_on_settings_page(widget)
    if isinstance(widget, QPushButton) and bool(widget.property("settingsInlineCell")):
        self._refresh_widget_style(widget)
        return
    if isinstance(widget, (QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QCheckBox, QPushButton)):
        widget.setProperty("settingsBubbleField", "true")
        if isinstance(widget, (QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit)):
            widget.setProperty("settingsEditableCell", "true")
            if on_settings_page:
                widget.setProperty("settingsValueCapsule", "true")
        if label_widget is not None:
            setattr(widget, "_paired_form_label", label_widget)
        if isinstance(widget, QLineEdit):
            widget.setMinimumWidth(360)
            widget.setMinimumHeight(34)
        elif isinstance(widget, (QComboBox, QSpinBox, QDoubleSpinBox, QPushButton)):
            widget.setMinimumHeight(34)
            if isinstance(widget, (QComboBox, QSpinBox, QDoubleSpinBox)) and on_settings_page:
                widget.setMinimumWidth(max(widget.minimumWidth(), 220))
                size_policy = widget.sizePolicy()
                widget.setSizePolicy(QSizePolicy.Expanding, size_policy.verticalPolicy())
        if on_settings_page and isinstance(widget, (QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit)):
            self._apply_settings_value_capsule_style(widget)
            if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                widget.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                spin_line_edit = widget.lineEdit()
                if spin_line_edit is not None:
                    spin_line_edit.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._refresh_widget_style(widget)
        return
    child_widgets = widget.findChildren(QWidget)
    for child in child_widgets:
        if child is widget:
            continue
        if isinstance(
            child,
            (QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QCheckBox, QPushButton),
        ):
            self._apply_settings_field_bubble(child, label_widget=label_widget)


def _apply_settings_form_bubbles(self, form_layout):
    if form_layout is None:
        return
    for row in range(form_layout.rowCount()):
        label_item = form_layout.itemAt(row, QFormLayout.LabelRole)
        label_widget = None
        if label_item and label_item.widget():
            label_widget = label_item.widget()
            field_item = form_layout.itemAt(row, QFormLayout.FieldRole)
            field_widget = field_item.widget() if field_item and field_item.widget() else None
            if field_widget is not None:
                copy_widget_owner_source(field_widget, label_widget, role=str(label_widget.text() or "").strip())
            label_widget.setProperty("settingsBubbleLabel", "true")
            label_widget.setMinimumHeight(34)
            self._refresh_widget_style(label_widget)

        field_item = form_layout.itemAt(row, QFormLayout.FieldRole)
        if field_item is None:
            continue
        if field_item.widget():
            self._apply_settings_field_bubble(field_item.widget(), label_widget=label_widget)
        elif field_item.layout():
            field_layout = field_item.layout()
            for i in range(field_layout.count()):
                child_item = field_layout.itemAt(i)
                if child_item.widget():
                    self._apply_settings_field_bubble(child_item.widget(), label_widget=label_widget)
