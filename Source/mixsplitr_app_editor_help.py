import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from mixsplitr_ui_browser import DragDropArea
from mixsplitr_ui_cards import GlassPage
from mixsplitr_ui_editor import TrackEditorPanel
from mixsplitr_devtools import annotate_widget

VB_CABLE_DOWNLOAD_URL = ""
BLACKHOLE_DOWNLOAD_URL = ""


def install_editor_help_methods(
    app_cls,
    *,
    vb_cable_download_url,
    blackhole_download_url,
):
    global VB_CABLE_DOWNLOAD_URL, BLACKHOLE_DOWNLOAD_URL

    VB_CABLE_DOWNLOAD_URL = str(vb_cable_download_url or "").strip()
    BLACKHOLE_DOWNLOAD_URL = str(blackhole_download_url or "").strip()

    methods = (
        create_track_editor_page,
        create_help_page,
        _load_track_editor_input_paths,
        _on_track_editor_workflow_action,
    )
    for method in methods:
        setattr(app_cls, method.__name__, method)


def create_track_editor_page(self):
    page = GlassPage()
    annotate_widget(page, role="Track Editor Page")
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
    header = QLabel("Track Editor")
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

    loader_card = self.create_base_card(
        "Load Tracks",
        "Drag in tracks or folders to edit metadata directly from this tab.",
    )
    self.track_editor_drop_area = DragDropArea(path_resolver=self._resolve_splitter_input_paths)
    self.track_editor_drop_area.label.setText("Drag & Drop audio files/folders here\nor click to browse")
    self.track_editor_drop_area.setMinimumHeight(132)
    self.track_editor_drop_area.paths_selected.connect(self._load_track_editor_input_paths)
    loader_card.layout().addWidget(self.track_editor_drop_area)
    self.track_editor_tab_status_label = QLabel()
    self.track_editor_tab_status_label.setWordWrap(True)
    self.track_editor_tab_status_label.setStyleSheet("color: #C4C7C5; font-size: 12px;")
    loader_card.layout().addWidget(self.track_editor_tab_status_label)
    layout.addWidget(loader_card)

    workspace_card = self.create_base_card(
        "Editor Workspace",
        "Review track list, edit metadata fields, and save or apply changes.",
    )
    self.track_editor_panel = TrackEditorPanel(self)
    self.track_editor_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    self.track_editor_panel.workflow_action.connect(self._on_track_editor_workflow_action)
    workspace_card.layout().addWidget(self.track_editor_panel, 1)
    layout.addWidget(workspace_card, 1)

    self._apply_card_line_surfaces_on_page(page)
    return self._wrap_page_in_transparent_scroll(page)


def create_help_page(self):
    page = GlassPage()
    annotate_widget(page, role="Help Page")
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
    header = QLabel("Help")
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

    workflow_card = self.create_base_card(
        "How it works/What to do",
        "MixSplitR is a combination of tools that work together like this: load audio, split it, identify what you can, review it, and export it.",
    )
    workflow_desc = QLabel(
        "The recommended steps are:\n\n"
        "1) Load a long recording, mix, capture, or ripped audio into Splitter.\n"
        "2) Pick one of the three splitter modes: Split Only, Split and Identify, or Timestamping.\n"
        "3) For split runs, MixSplitR asks whether to use Full or Light preview caching before processing. Timestamping skips that choice.\n"
        "4) Choose a split mode. Assisted is the best starting point for most people. If Long Track Prompt is enabled and MixSplitR catches a very long file, it can also ask whether to treat it as a single track or split it as a mix, then Continuous Mix vs Silence Detection.\n"
        "5) If that mode needs identification, continue to Identifier and choose the method there. Timestamping with No-ID Labels can run directly from Splitter.\n"
        "6) Let MixSplitR process the audio or build the tracklist.\n"
        "7) Review the results in Editor, fix anything that's off, and import a tracklist if you have one.\n"
        "8) Export the finished tracks or save the finished tracklist.\n\n"
        "Everything else fits around that process: Recorder creates the source file first, CD Ripping feeds disc "
        "audio into the same pipeline, Identifier is for tracks that are already separate or queued from Splitter, "
        "and long mixes should start in Splitter first. "
        "Timestamping is for mixes where you want a tracklist instead of split audio, and Session History is for "
        "reviewing or managing past runs."
    )
    workflow_desc.setProperty("helpDescriptionCell", True)
    workflow_desc.setWordWrap(True)
    workflow_desc.setStyleSheet("color: #C4C7C5; font-size: 13px;")
    workflow_card.layout().addWidget(workflow_desc)
    layout.addWidget(workflow_card)

    first_run_card = self.create_base_card(
        "Getting Started",
        "A quick setup checklist so the recommended steps work smoothly on your first run.",
    )
    first_run_steps = QLabel(
        "1) Head to Settings > Directory Settings and pick an Output Folder.\n"
        "2) In Splitter, choose one of the three modes.\n"
        "3) If you picked a mode with ID, go to Identifier and choose the method you want to use.\n"
        "4) Enter the API keys that method needs. If you just want clean splits or NN timestamps, you can skip this.\n"
        "5) In the Splitter tab, choose Full or Light preview when prompted. Timestamping skips that prompt and still lands in Editor review before save.\n"
        "6) Start with Assisted split mode unless you already know you want Automatic or full manual control.\n"
        "7) Hit Start Processing, then review the results in Editor before exporting."
    )
    first_run_steps.setProperty("helpDescriptionCell", True)
    first_run_steps.setWordWrap(True)
    first_run_steps.setStyleSheet("color: #C4C7C5; font-size: 13px;")
    first_run_card.layout().addWidget(first_run_steps)
    layout.addWidget(first_run_card)

    links_card = self.create_base_card(
        "API Setup",
        "Grab the keys your mode needs. Most are free.",
    )
    api_links = QLabel(
        '<a href="https://console.acrcloud.com/signup" style="color:#E04040;">ACRCloud</a> - '
        "Sign up for Host, Access Key, and Secret Key.<br>"
        '<a href="https://acoustid.org/api-key" style="color:#E04040;">AcoustID</a> - '
        "Free API key for fingerprint matching. Highly recommended.<br>"
        '<a href="https://www.last.fm/api/account/create" style="color:#E04040;">Last.fm</a> - '
        "Optional key for richer genre and tag data."
    )
    api_links.setTextFormat(Qt.RichText)
    api_links.setTextInteractionFlags(Qt.TextBrowserInteraction)
    api_links.setOpenExternalLinks(True)
    api_links.setProperty("helpDescriptionCell", True)
    api_links.setWordWrap(True)
    api_links.setStyleSheet("color: #C4C7C5; font-size: 13px;")
    links_card.layout().addWidget(api_links)
    links_hint = QLabel("Shazam and MusicBrainz search work without any account or key.")
    links_hint.setProperty("helpDescriptionCell", True)
    links_hint.setWordWrap(True)
    links_hint.setStyleSheet("color: #C4C7C5; font-size: 13px;")
    links_card.layout().addWidget(links_hint)
    setup_guide_link = QLabel(
        '<a href="https://github.com/chefkjd/MixSplitR/blob/main/API%20key%20setup%20guide.md" '
        'style="color:#E04040;">Step-by-step API key setup guide</a>'
    )
    setup_guide_link.setTextFormat(Qt.RichText)
    setup_guide_link.setTextInteractionFlags(Qt.TextBrowserInteraction)
    setup_guide_link.setOpenExternalLinks(True)
    setup_guide_link.setProperty("helpDescriptionCell", True)
    setup_guide_link.setWordWrap(True)
    setup_guide_link.setStyleSheet("color: #C4C7C5; font-size: 13px;")
    links_card.layout().addWidget(setup_guide_link)
    layout.addWidget(links_card)

    recording_help_card = self.create_base_card(
        "Recording Setup",
        "How to capture whatever's playing on your computer.",
    )
    recording_steps = QLabel(
        "Windows: App Capture and Background Capture are Windows-only. Pick an entry labeled "
        "'(App Capture)' to record a specific app like Spotify "
        "or Apple Music. App Capture entries only appear for apps that are playing audio right "
        "now, so start playback first and then click Refresh Sources. Pick an entry labeled "
        "'(WASAPI loopback)' if you want to record everything you hear. For silent background "
        "recording, switch the recorder page to Background Capture, choose Stereo to "
        "match your cable device, use a cable-style device "
        f'such as <a href="{VB_CABLE_DOWNLOAD_URL}" style="color:#E04040;">VB-Cable</a>, '
        "then send the app to that same cable device in Windows Volume Mixer. If you still want "
        "to hear it, turn on Hear Through Speakers While Recording. "
        "Press Start Recording and watch the Signal meter to make sure audio is coming "
        "through. If the app or device still does not show up, refresh sources again or relaunch "
        "MixSplitR.<br><br>"
        f"macOS: App Capture and Background Capture are not available on macOS. Install "
        f'<a href="{BLACKHOLE_DOWNLOAD_URL}" style="color:#E04040;">BlackHole 2ch</a> '
        "(recommended), "
        "then create a Multi-Output Device in Audio MIDI Setup that combines your speakers with "
        "BlackHole. Set that as your macOS output, pick BlackHole as the input in MixSplitR, "
        "and record. Switch your output back to normal when you're done."
    )
    recording_steps.setTextFormat(Qt.RichText)
    recording_steps.setTextInteractionFlags(Qt.TextBrowserInteraction)
    recording_steps.setOpenExternalLinks(False)
    recording_steps.linkActivated.connect(self._handle_recording_help_link)
    recording_steps.setProperty("helpDescriptionCell", True)
    recording_steps.setWordWrap(True)
    recording_steps.setStyleSheet("color: #C4C7C5; font-size: 13px;")
    self.recording_setup_help_label = recording_steps
    self._refresh_recording_help_text()
    recording_help_card.layout().addWidget(recording_steps)
    layout.addWidget(recording_help_card)

    cd_ripping_help_card = self.create_base_card(
        "CD Ripping",
        "For ripping audio CDs directly into MixSplitR.",
    )
    cd_ripping_steps = QLabel(
        "Open the CD Ripping tab, click Detect Drives, and select yours (or enter a path "
        "manually). Tweak output format and options in Settings > CD Ripping Options, then "
        "hit Start Ripping. When it finishes, use the checked ripped-track list to send files "
        "straight to Identifier or Editor."
    )
    cd_ripping_steps.setProperty("helpDescriptionCell", True)
    cd_ripping_steps.setWordWrap(True)
    cd_ripping_steps.setStyleSheet("color: #C4C7C5; font-size: 13px;")
    cd_ripping_help_card.layout().addWidget(cd_ripping_steps)
    layout.addWidget(cd_ripping_help_card)

    pipeline_card = self.create_base_card(
        "What Happens When You Process",
        "A quick look at the steps MixSplitR runs through behind the scenes.",
    )
    pipeline_steps = QLabel(
        "First, your files are analyzed to figure out which ones are long mixes and which are "
        "already single tracks. If Long Track Prompt is enabled, very long files can also be "
        "overridden to stay single tracks or be forced into mix splitting. Mixes get split "
        "based on your chosen split mode: Automatic uses silence detection, Assisted and Manual "
        "use the waveform editor, and Continuous Mix can reuse the timestamp transition detector "
        "when you choose that path for a long file. Then each "
        "track goes through identification unless you picked Split Only. Timestamping builds "
        "a tracklist instead of split audio and can either identify entries or use generic "
        "Track NN labels, depending on the Timestamping settings. "
        "Metadata from MusicBrainz, iTunes, Deezer, and Last.fm fills in the blanks. Finally, "
        "everything is exported and a session manifest is saved so you can review it later.\n\n"
        "If Preserve Source Format is on, single tracks keep their original format and just get "
        "fresh tags without re-encoding. Timestamping mode skips the audio export entirely and "
        "writes a tracklist with timestamps instead."
    )
    pipeline_steps.setProperty("helpDescriptionCell", True)
    pipeline_steps.setWordWrap(True)
    pipeline_steps.setStyleSheet("color: #C4C7C5; font-size: 13px;")
    pipeline_card.layout().addWidget(pipeline_steps)
    layout.addWidget(pipeline_card)

    essentia_help_card = self.create_base_card(
        "Essentia Assist + Genre Help",
        "Built-in music analysis that helps with transitions and genre tags.",
    )
    essentia_help = QLabel(
        "Essentia is a music analysis engine bundled with MixSplitR. It does two things:\n\n"
        "Transition Assist: In Timestamping and Continuous Mix boundary detection, Essentia "
        "listens for changes in the audio to help figure out where one song ends and the next "
        "begins. The quick Assist Strength setting controls how aggressive the Essentia layer is: "
        "Conservative catches fewer but more reliable boundaries, Balanced is a good default, and "
        "Aggressive catches more but may over-split. Advanced Timestamping also has Transition "
        "Preset choices like Balanced, Electronic / DJ Mix, and Rock / Band Mix for the broader "
        "detector. If you've tweaked the advanced numbers yourself, the preset shows as Custom.\n\n"
        "Genre Help: When enabled, Essentia tries to tag each identified track with a genre. "
        "Turn on 'Only Fill Missing' if you only want it to fill in genres for tracks that "
        "don't already have one.\n\n"
        "Power users can reveal the full set of Essentia knobs with 'Show Advanced Essentia "
        "Controls' in Settings."
    )
    essentia_help.setProperty("helpDescriptionCell", True)
    essentia_help.setWordWrap(True)
    essentia_help.setStyleSheet("color: #C4C7C5; font-size: 13px;")
    essentia_help_card.layout().addWidget(essentia_help)
    layout.addWidget(essentia_help_card)

    preserve_format_help_card = self.create_base_card(
        "Preserve Source Format",
        "Keep your original file format and just update the tags.",
    )
    preserve_format_help = QLabel(
        "When this is on (Settings > Directory Settings), single tracks that pass through "
        "without splitting keep their original format. MixSplitR writes updated metadata "
        "without re-encoding the audio. Split mix chunks still export in your chosen output "
        "format. If the retag fails for any reason, it falls back to a normal re-encode "
        "automatically. This setting doesn't affect Timestamping mode since that only writes "
        "tracklist files."
    )
    preserve_format_help.setProperty("helpDescriptionCell", True)
    preserve_format_help.setWordWrap(True)
    preserve_format_help.setStyleSheet("color: #C4C7C5; font-size: 13px;")
    preserve_format_help_card.layout().addWidget(preserve_format_help)
    layout.addWidget(preserve_format_help_card)

    def _build_mode_help_card(title, summary, steps_text):
        card = self.create_base_card(title, summary)
        steps_label = QLabel(steps_text)
        steps_label.setProperty("helpDescriptionCell", True)
        steps_label.setWordWrap(True)
        steps_label.setStyleSheet("color: #C4C7C5; font-size: 13px;")
        card.layout().addWidget(steps_label)
        return card

    mode_cards = [
        _build_mode_help_card(
            "Split Only (No ID)",
            "Just cut the mix into separate tracks. No lookups, totally free.",
            "Uses the selected split mode. Automatic uses silence detection, Assisted preloads "
            "detected boundaries into the waveform editor, and Manual lets you place markers "
            "yourself. If Long Track Prompt is enabled and you choose Split as Mix, you can also "
            "pick Continuous Mix to reuse the timestamp transition detector instead of silence "
            "detection. Each piece is saved as Track 01, Track 02, etc. No internet connection "
            "or accounts needed.\n\n"
            "Best for when you already know the track names and just need clean files, or "
            "you want quick splits before deciding what to do with them.",
        ),
        _build_mode_help_card(
            "Split and Identify",
            "Split the mix, then choose an Identifier and run the full metadata pass.",
            "Use this when you want separated tracks with artist/title metadata. The Splitter page "
            "handles the mode choice, then Identifier is where you choose MusicBrainz / AcoustID, "
            "ACRCloud, or Dual before the queued run starts.\n\n"
            "Split runs still end in Track Editor review. Before processing, MixSplitR asks "
            "whether to build a Full or Light preview cache first.",
        ),
        _build_mode_help_card(
            "Timestamping",
            "Build a timestamped tracklist instead of exporting split audio files.",
            "Scans the mix for boundaries, opens the Track Editor for review, and saves a "
            "timestamp list instead of exporting split audio files.\n\n"
            "By default it can queue through Identifier so MixSplitR can try to name each "
            "entry using MusicBrainz / AcoustID, ACRCloud, or Dual. If you want no ID and "
            "just generic names, turn on 'Enable Track NN labels only' in Timestamping "
            "settings. That keeps the run in timestamping mode but skips Identifier.",
        ),
        _build_mode_help_card(
            "Identifier",
            "Use to select identification mode for mix-splitting, or load in any single tracks to run through the identifier.",
            "Identifier is for two things: identifying tracks that are already separate, and "
            "continuing queued runs from Splitter that still need an identification method.\n\n"
            "If you drop one long file here, MixSplitR can ask whether to send it to Splitter so "
            "you can choose a mode and split type. If you keep it in Identifier, it is treated "
            "as one track and then reviewed in Track Editor.\n\n"
            "MusicBrainz / AcoustID is the free/default option, ACRCloud is commercial, and "
            "Dual runs ACRCloud plus AcoustID together and keeps the better result.",
        ),
        _build_mode_help_card(
            "Editor",
            "Review results, fix metadata, and finalize tracks or timestamp lists before saving.",
            "Editor is the final review workspace before export or tracklist save. Depending on the "
            "mode, you can correct artist, title, album, genre, BPM, year, ISRC, filenames, and "
            "timestamp fields.\n\n"
            "Bulk Edit lets you apply shared metadata to checked tracks at once, and you can also load "
            "standalone audio files directly into the Editor tab when you just want to retag or clean up "
            "existing files without running the full splitter.",
        ),
    ]

    for card in mode_cards:
        card.setMinimumWidth(0)
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    for idx in range(0, len(mode_cards), 2):
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(20)
        self._track_layout_metrics(row, base_margins=(0, 0, 0, 0), base_spacing=20)
        row.addWidget(mode_cards[idx], 1)
        if idx + 1 < len(mode_cards):
            row.addWidget(mode_cards[idx + 1], 1)
        else:
            row.addStretch(1)
        layout.addLayout(row)

    settings_reference_card = self.create_base_card(
        "Settings Quick Reference",
        "A rundown of what the main settings groups control.",
    )
    settings_reference = QLabel(
        "Identification Settings + API Key Settings: Fingerprint Sample sets how much audio is "
        "sampled for matching (8-45 sec). Probe Mode (ACRCloud) switches between a single center "
        "sample and multi-point probes. Enable Shazam toggles the Shazam fallback used in "
        "identification and timestamping. API keys are only needed for the services you actually use.\n\n"
        "Directory Settings: Output Folder, Timestamp Output Folder, export format, rename/folder "
        "preset, Preserve Source Format, recording folder, temp workspace, session history, and "
        "CD-rip output all live here.\n\n"
        "Essentia (Simple) + Advanced Timestamping: Essentia Assist and Assist Strength control "
        "transition help. Transition Preset switches between Balanced, Electronic / DJ Mix, "
        "Rock / Band Mix, and Custom. Window size, step size, min segment, fallback interval, "
        "confidence thresholds, and backtrack fine-tune Timestamping and Continuous Mix detection. "
        "Turning on 'Enable Track NN labels only' keeps Timestamping in generic-label mode "
        "without queueing Identifier.\n\n"
        "Genre Enrichment + General Behavior + Advanced Toggles: Genre/BPM enrichment, album "
        "search, artist normalization, deep folder scan, split sensitivity, split seek step, "
        "duplicate handling, long-track prompting, and Debug Readout live here. CD Ripping "
        "Options controls rip format, auto metadata, and eject-when-done.\n\n"
        "All settings stay visible on the Settings page now; they are no longer hidden behind a "
        "per-mode 'Settings for' selector."
    )
    settings_reference.setProperty("helpDescriptionCell", True)
    settings_reference.setWordWrap(True)
    settings_reference.setStyleSheet("color: #C4C7C5; font-size: 13px;")
    settings_reference_card.layout().addWidget(settings_reference)
    layout.addWidget(settings_reference_card)

    layout.addStretch()
    self._apply_card_line_surfaces_on_page(page)
    return self._wrap_page_in_transparent_scroll(page)


def _load_track_editor_input_paths(self, paths):
    panel = getattr(self, "track_editor_panel", None)
    if panel is None:
        return 0
    resolved = self._resolve_splitter_input_paths(paths)
    if not resolved:
        if hasattr(self, "track_editor_tab_status_label"):
            self.track_editor_tab_status_label.setText("No supported audio files found")
        return 0

    if panel.editor_mode in ("preview", "timestamp_preview") and panel.has_unsaved_changes():
        proceed = QMessageBox.question(
            self,
            "Replace Preview Session",
            "Replacing the current preview editor session will discard unsaved edits. Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if proceed != QMessageBox.Yes:
            return 0
    elif panel.editor_mode == "standalone" and panel.has_unsaved_changes():
        proceed = QMessageBox.question(
            self,
            "Replace Track List",
            "Replace current unsaved standalone edits with the newly loaded files?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if proceed != QMessageBox.Yes:
            return 0

    loaded = panel.load_standalone_files(resolved)
    self.pending_preview_open_editor = False
    self.pending_preview_cache_path = ""
    self.pending_preview_temp_folder = ""
    if hasattr(self, "track_editor_tab_status_label"):
        self.track_editor_tab_status_label.setText(f"Loaded {loaded} file(s) into Track Editor")
    return loaded


def _on_track_editor_workflow_action(self, action):
    action_text = str(action or "").strip().lower()
    panel = getattr(self, "track_editor_panel", None)
    if panel is None:
        return

    def _reset_track_editor_workspace():
        panel._reset_to_empty_standalone_editor()
        if hasattr(self, "track_editor_tab_status_label"):
            self.track_editor_tab_status_label.setText("Track Editor ready")

    if panel.editor_mode == "preview":
        self.pending_preview_cache_path = str(panel.cache_path or self.pending_preview_cache_path)
        self.pending_preview_temp_folder = str(panel.preview_temp_folder or self.pending_preview_temp_folder)
        self._on_preview_editor_finished(action_text)
        if action_text in ("done", "quit"):
            _reset_track_editor_workspace()
        elif (
            action_text == "apply"
            and self.stacked_widget.currentIndex() == self.track_editor_page_index
        ):
            self.switch_page(0)
        return

    if panel.editor_mode == "timestamp_preview":
        self.pending_preview_cache_path = str(panel.cache_path or self.pending_preview_cache_path)
        self.pending_preview_temp_folder = str(panel.preview_temp_folder or self.pending_preview_temp_folder)
        self._on_timestamp_preview_editor_finished(action_text)
        if action_text in ("done", "quit"):
            _reset_track_editor_workspace()
        return

    if panel.editor_mode == "session":
        if action_text in ("done", "quit"):
            if action_text == "done":
                self.status_label.setText("Status: Session record saved")
            else:
                self.status_label.setText("Status: Session editor closed")
            manifest_path = str(panel.session_manifest_path or "")
            self._refresh_session_history_list(select_path=manifest_path)
            if manifest_path and action_text == "done":
                updated = self._load_manifest_from_path(manifest_path)
                if updated:
                    self.history_output.setPlainText(self._render_manifest_details_text(updated))
            _reset_track_editor_workspace()
        return

    if action_text == "done":
        self.status_label.setText("Status: Track Editor session saved")
