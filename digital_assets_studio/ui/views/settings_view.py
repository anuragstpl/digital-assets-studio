"""Settings: keys, model routing, publishing connections."""
from __future__ import annotations

import flet as ft

from ...config import ALL_ROLES, IMAGE_ROLES, TEXT_ROLES
from ...core import keyvault
from ...core.llm import router
from ...core.publishing import aivideo, appstore, mpt, play, stockvideo, tts, video
from ...core.publishing import browser as browser_mod
from ...core.publishing import youtube as yt
from ...core.settings import (IMAGE_KINDS, load as load_settings, retarget_unkeyed_roles,
                              save as save_settings, set_all_roles, usable)
from ...theme import RADIUS, RADIUS_SM
from ..components import (body, card, divider, dropdown, ghost_button, h1, h2, label,
                          pill, primary_button, snack, text_field)


def build(studio) -> ft.Control:
    p = studio.palette
    s = load_settings()

    tabs = ft.Tabs(
        selected_index=studio.settings_tab,
        on_change=lambda e: studio.set_settings_tab(e.control.selected_index),
        indicator_color=p.accent, label_color=p.text, unselected_label_color=p.text_faint,
        divider_color=p.line,
        tabs=[
            ft.Tab(text="Providers", content=_providers(studio, s)),
            ft.Tab(text="Model routing", content=_routing(studio, s)),
            ft.Tab(text="Publishing", content=_publishing(studio, s)),
            ft.Tab(text="General", content=_general(studio, s)),
        ],
        expand=True,
    )
    return ft.Column([h1(p, "Settings"), tabs], spacing=12, expand=True)


# ------------------------------------------------------------------ providers --

def _providers(studio, s) -> ft.Control:
    p = studio.palette
    secure = keyvault.is_secure()
    banner = ft.Container(
        content=ft.Row([
            ft.Icon(ft.Icons.LOCK_ROUNDED if secure else ft.Icons.WARNING_AMBER_ROUNDED,
                    size=16, color=p.ok if secure else p.warn),
            ft.Text(("Keys are stored in your OS credential store: " if secure else
                     "No OS keychain was found, so keys go into an encoded file in your workspace. "
                     "That is obfuscation, not encryption — ") + keyvault.backend_name(),
                    size=12, color=p.text_muted, expand=True),
        ], spacing=9),
        padding=12, border_radius=RADIUS_SM,
        bgcolor=ft.Colors.with_opacity(0.09, p.ok if secure else p.warn))

    cards = [banner]
    for prov in s.providers:
        cards.append(_provider_card(studio, s, prov))
    return ft.Container(
        content=ft.Column(cards, spacing=12, scroll=ft.ScrollMode.AUTO, expand=True),
        padding=ft.padding.only(top=16, right=6), expand=True)


def _provider_card(studio, s, prov) -> ft.Control:
    p = studio.palette
    key_field = text_field(
        p, "API key", keyvault.get_secret(prov.secret_name), password=True,
        hint="Paste it here — it is written straight to the key store",
        helper="Leave blank if this endpoint needs no key")
    model_field = text_field(p, "Default model", prov.default_model)
    base_field = text_field(p, "Base URL", prov.base_url,
                            helper="Only used by OpenAI-compatible endpoints")
    enabled = ft.Switch(value=prov.enabled, active_color=p.accent)

    status = ft.Text("", size=12, color=p.text_muted, selectable=True, expand=True)
    key_badge = ft.Text("", size=11, color=p.ok)

    def _apply_fields() -> None:
        keyvault.set_secret(prov.secret_name, (key_field.value or "").strip())
        prov.default_model = (model_field.value or "").strip()
        prov.base_url = (base_field.value or "").strip()
        prov.enabled = enabled.value

    def _say(text: str, tone: str = "muted") -> None:
        status.value = text
        status.color = {"ok": p.ok, "error": p.danger, "muted": p.text_muted}[tone]
        key_badge.value = "key saved" if keyvault.has_secret(prov.secret_name) else ""
        studio.page.update()

    def save(e):
        _apply_fields()
        moved = retarget_unkeyed_roles(s)
        save_settings(s)
        if moved:
            _say(f"Saved. {moved} role(s) had no working provider and now point at "
                 f"{prov.label}. Check Model routing if you want them elsewhere.", "ok")
        else:
            _say(f"{prov.label} saved.", "ok")

    def use_everywhere(e):
        _apply_fields()
        if not usable(prov):
            _say("Save a key for this provider first — routing to it would fail.", "error")
            return
        moved = set_all_roles(s, prov.id)
        save_settings(s)
        _say(f"Every {'image' if prov.kind in IMAGE_KINDS else 'text'} role now uses "
             f"{prov.label} ({prov.default_model or 'no model set'}). {moved} role(s) updated.", "ok")

    def test(e):
        _apply_fields()
        save_settings(s)
        _say("Testing…")

        def work(ctx):
            return router.test_provider(prov)

        def done(update):
            if update.status == "done":
                _say(str(update.result), "ok")
            else:
                _say(update.message, "error")

        # rerender=False so the page does not rebuild and throw you back to the top
        studio.submit(f"Test {prov.label}", work, done, rerender=False)

    kind_label = {"anthropic": "Anthropic API", "openai": "OpenAI API", "google": "Gemini API",
                  "openai_compat": "OpenAI-compatible", "gemini_image": "Imagen",
                  "openai_image": "OpenAI images", "sd_webui": "Local Stable Diffusion"}

    key_badge.value = "key saved" if keyvault.has_secret(prov.secret_name) else ""
    roles_here = [t for rid, t, _ in ALL_ROLES if s.route(rid).provider_id == prov.id]

    rows: list[ft.Control] = [
        ft.Row([
            ft.Text(prov.label, size=15, weight=ft.FontWeight.W_600, color=p.text),
            pill(p, kind_label.get(prov.kind, prov.kind), p.accent),
            key_badge,
            ft.Container(expand=True),
            ft.Text(f"{len(roles_here)} role(s)" if roles_here else "unused",
                    size=11, color=p.text_faint),
            ft.Text("enabled", size=11, color=p.text_faint), enabled,
        ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
    ]
    if prov.kind != "sd_webui":
        rows.append(key_field)
    rows.append(ft.Row([ft.Container(model_field, expand=1),
                        ft.Container(base_field, expand=1)], spacing=12))
    rows.append(ft.Row([primary_button(p, "Save", save, ft.Icons.SAVE_ROUNDED),
                        ghost_button(p, "Test connection", test, ft.Icons.WIFI_TETHERING_ROUNDED),
                        ghost_button(p, "Use for every role", use_everywhere,
                                     ft.Icons.ALT_ROUTE_ROUNDED)],
                       spacing=10, wrap=True, run_spacing=8))
    rows.append(ft.Row([status], vertical_alignment=ft.CrossAxisAlignment.CENTER))
    return card(p, *rows)


# -------------------------------------------------------------------- routing --

def _routing(studio, s) -> ft.Control:
    p = studio.palette
    text_opts = [(f"{x.label}", x.id) for x in s.text_providers() if x.enabled]
    image_opts = [(f"{x.label}", x.id) for x in s.image_providers() if x.enabled]

    def row(role_id: str, title: str, desc: str, opts):
        route = s.route(role_id)
        labels = [o[0] for o in opts] or ["(no provider enabled)"]
        current = next((o[0] for o in opts if o[1] == route.provider_id), labels[0])

        def on_provider(e):
            pick = next((o[1] for o in opts if o[0] == e.control.value), "")
            route.provider_id = pick
            prov = s.provider(pick)
            if prov:
                # follow the provider's default model unless you typed your own
                route.model = prov.default_model
                model_box.value = route.model
            save_settings(s)
            studio.page.update()   # in place - never rebuild the whole page here

        def on_model(e):
            route.model = e.control.value.strip()
            save_settings(s)

        def on_temp(e):
            try:
                route.temperature = float(e.control.value)
            except ValueError:
                route.temperature = 0.7
            save_settings(s)

        def on_max(e):
            try:
                route.max_tokens = int(float(e.control.value))
            except ValueError:
                route.max_tokens = 4096
            save_settings(s)

        model_box = text_field(p, "Model", route.model, on_change=on_model)

        return card(
            p,
            ft.Row([
                ft.Column([ft.Text(title, size=14, weight=ft.FontWeight.W_600, color=p.text),
                           ft.Text(desc, size=12, color=p.text_muted)],
                          spacing=2, tight=True, expand=True),
            ]),
            ft.Row([
                ft.Container(dropdown(p, "Provider", labels, current, on_provider), expand=2),
                ft.Container(model_box, expand=3),
                ft.Container(text_field(p, "Temp", str(route.temperature), on_change=on_temp), expand=1),
                ft.Container(text_field(p, "Max tokens", str(route.max_tokens), on_change=on_max), expand=1),
            ], spacing=10),
            gap=10)

    blocks: list[ft.Control] = [
        ft.Container(content=body(p, "Every job in the suite asks for a role, not a model. Point the "
                                     "cheap roles at a cheap or local model and keep the expensive one "
                                     "for drafting — that single choice is most of your running cost.",
                                  muted=True, size=12),
                     padding=ft.padding.only(bottom=4)),
    ]
    blocks += [row(rid, title, desc, text_opts) for rid, title, desc in TEXT_ROLES]
    blocks.append(ft.Container(content=label(p, "Images"), padding=ft.padding.only(top=8, left=4)))
    blocks += [row(rid, title, desc, image_opts) for rid, title, desc in IMAGE_ROLES]
    blocks.append(ft.Container(height=20))
    return ft.Container(content=ft.Column(blocks, spacing=10, scroll=ft.ScrollMode.AUTO, expand=True),
                        padding=ft.padding.only(top=16, right=6), expand=True)


# ----------------------------------------------------------------- publishing --

def _status_line(p, ok: bool, ok_text: str, no_text: str) -> ft.Control:
    return ft.Row([
        ft.Icon(ft.Icons.CHECK_CIRCLE_ROUNDED if ok else ft.Icons.RADIO_BUTTON_UNCHECKED_ROUNDED,
                size=15, color=p.ok if ok else p.text_faint),
        ft.Text(ok_text if ok else no_text, size=12, color=p.text_muted, expand=True),
    ], spacing=8)


def _channel_row(studio, acc, is_default: bool) -> ft.Control:
    """One connected channel, with the two things you can do to it."""
    p = studio.palette

    def make_default(e):
        yt.set_default(acc.slug)
        snack(studio.page, p, f"{acc.display} is now the default channel", "ok")
        studio.refresh()

    def forget(e):
        yt.remove_account(acc.slug)
        snack(studio.page, p, f"Disconnected {acc.display}", "ok")
        studio.refresh()

    def recheck(e):
        studio.submit(f"Read {acc.display}",
                      lambda ctx, slug=acc.slug: yt.refresh_account(slug),
                      lambda u: (studio.toast(
                          f"Now reading as {u.result.display}" if u.status == "done"
                          else u.message, "ok" if u.status == "done" else "error"),
                          studio.refresh()))

    actions: list[ft.Control] = []
    if not is_default:
        actions.append(ghost_button(p, "Make default", make_default,
                                    ft.Icons.STAR_OUTLINE_ROUNDED))
    actions.append(ghost_button(p, "Re-read", recheck, ft.Icons.REFRESH_ROUNDED))
    actions.append(ghost_button(p, "Disconnect", forget, ft.Icons.LINK_OFF_ROUNDED, danger=True))

    return ft.Container(
        content=ft.Row([
            ft.Icon(ft.Icons.SMART_DISPLAY_ROUNDED, size=17, color=p.video),
            ft.Column([
                ft.Row([ft.Text(acc.title or acc.label or acc.slug, size=14,
                                weight=ft.FontWeight.W_600, color=p.text),
                        pill(p, "default", p.ok) if is_default else ft.Container(width=0)],
                       spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Text(acc.handle or acc.channel_id or "signed in, channel not read yet",
                        size=11, color=p.text_faint),
            ], spacing=2, tight=True, expand=True),
            ft.Row(actions, spacing=6, wrap=True, run_spacing=6),
        ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        padding=ft.padding.symmetric(10, 12), border_radius=RADIUS_SM, bgcolor=p.surface_alt)


def _youtube(studio) -> ft.Control:
    """The YouTube card: one OAuth client, as many channels as you run.

    A YouTube token is bound to one channel - the picker in the browser is what
    chooses it - so several channels means several sign-ins through the same
    client, listed here, and the upload step picks between them.
    """
    p = studio.palette
    cid, csec = yt.oauth_client()
    yt_id = text_field(p, "OAuth client ID", cid)
    yt_secret = text_field(p, "OAuth client secret", csec, password=True)
    live = yt.connected_accounts()
    default = yt.default_slug()

    def save_yt(e):
        yt.save_client(yt_id.value.strip(), yt_secret.value.strip())
        snack(studio.page, p, "YouTube client saved", "ok")
        studio.refresh()

    def connect_first(e):
        studio.submit("Connect YouTube", lambda ctx: yt.connect(),
                      lambda u: studio.after_connect(u, "YouTube"))

    def connect_another(e):
        if not cid or not csec:
            snack(studio.page, p, "Save the OAuth client ID and secret first.", "error")
            return

        def work(ctx):
            acc = yt.add_account("")
            ctx.log("Sign in with the Google account that owns the channel you want, "
                    "and pick that channel on the chooser.")
            return yt.connect(acc.slug)

        studio.submit("Connect another channel", work,
                      lambda u: studio.after_connect(u, "The channel"))

    rows: list[ft.Control] = [
        ft.Row([h2(p, "YouTube"), pill(p, "uploads, thumbnails, captions", p.video),
                ft.Container(expand=True)], spacing=8),
        _status_line(p, bool(live),
                     f"{len(live)} channel(s) connected — uploads run from inside the suite."
                     if live else "",
                     "Not connected yet."),
    ]
    if live:
        rows.append(ft.Column([_channel_row(studio, a, a.slug == default) for a in live],
                              spacing=6, tight=True))
        if len(live) > 1:
            rows.append(body(p, "Each project picks its channel in the 'YouTube channel' box on "
                                "the upload step. Until it does, the default above is used.",
                             muted=True, size=12))
    rows.append(
        body(p, "Enable the YouTube Data API v3 in Google Cloud, then create an OAuth client of "
                "type Desktop app — the Web type cannot sign in from a desktop app and will be "
                "rejected. Paste both values here.\n\n"
                "Before you connect, publish the OAuth consent screen to In production. Left in "
                "Testing, Google expires the sign-in after 7 days and uploads start failing with "
                "invalid_grant a week later. The 'unverified app' warning you then see at sign-in "
                "is expected — Advanced, then Go to your app.\n\n"
                "One sign-in is one channel: the API has no way to switch channels on an upload, "
                "so if you run several, connect each one and pick between them per project.",
             muted=True, size=12))
    rows.append(
        ft.Row([ghost_button(p, "Enable the API",
                             lambda e: studio.open_url(
                                 "https://console.cloud.google.com/apis/library/youtube.googleapis.com"),
                             ft.Icons.OPEN_IN_NEW_ROUNDED),
                ghost_button(p, "Consent screen",
                             lambda e: studio.open_url(
                                 "https://console.cloud.google.com/apis/credentials/consent"),
                             ft.Icons.OPEN_IN_NEW_ROUNDED),
                ghost_button(p, "Credentials",
                             lambda e: studio.open_url(
                                 "https://console.cloud.google.com/apis/credentials"),
                             ft.Icons.OPEN_IN_NEW_ROUNDED)],
               spacing=8, wrap=True, run_spacing=8))
    rows.append(ft.Row([ft.Container(yt_id, expand=1), ft.Container(yt_secret, expand=1)],
                       spacing=12))
    rows.append(ft.Row([
        primary_button(p, "Save", save_yt, ft.Icons.SAVE_ROUNDED),
        ghost_button(p, "Connect a channel" if not live else "Connect another channel",
                     connect_first if not live else connect_another, ft.Icons.LINK_ROUNDED),
    ], spacing=10, wrap=True, run_spacing=8))
    return card(p, *rows)


def _ai_video(studio) -> ft.Control:
    """OpenRouter as the video engine: the key already lives in Providers, so this
    card exists to answer the only other question - which model, and at what cost."""
    p = studio.palette
    status = ft.Text("", size=12, color=p.text_muted, selectable=True, expand=True)
    listing = ft.Column([], spacing=3, tight=True)

    def _say(text: str, tone: str = "muted") -> None:
        status.value = text
        status.color = {"ok": p.ok, "error": p.danger, "muted": p.text_muted}[tone]
        studio.page.update()

    def show(models: list[dict]) -> None:
        listing.controls = [
            ft.Text(f"{m['id']}"
                    + (f"  ·  {m['modality']}" if m.get("modality") else ""),
                    size=11, color=p.text_muted, selectable=True)
            for m in models[:40]]
        studio.page.update()

    def list_video(e):
        _say("Asking OpenRouter what it serves…")
        studio.submit("List OpenRouter video models",
                      lambda ctx: aivideo.list_models("video", refresh=True),
                      lambda u: (show(u.result) if u.status == "done" else None,
                                 _say(f"{len(u.result)} video models available — paste an id "
                                      f"into the 'Video model' box on the AI footage step."
                                      if u.status == "done" else u.message,
                                      "ok" if u.status == "done" else "error")),
                      rerender=False)

    def test_key(e):
        _say("Testing…")
        studio.submit("Test OpenRouter", lambda ctx: aivideo.test(),
                      lambda u: _say(str(u.result) if u.status == "done" else u.message,
                                     "ok" if u.status == "done" else "error"),
                      rerender=False)

    return card(
        p,
        ft.Row([h2(p, "AI video"), pill(p, "video engine", p.video),
                ft.Container(expand=True)], spacing=8),
        _status_line(p, aivideo.has_key(),
                     "OpenRouter key saved — the AI video engine is available.",
                     "No OpenRouter key yet. Add it in Settings › Providers › OpenRouter."),
        body(p, "The AI video engine generates original footage through OpenRouter, which puts "
                "Veo, Sora, Kling, Seedance, Wan, Hailuo and the rest behind the one key you "
                "already use for text. It is the only engine here that costs real money per "
                "video: expect a few cents to a few dollars for a short episode depending on "
                "the model and resolution, so start at 720p with a small clip count and look at "
                "the result before scaling it up.\n\n"
                f"The default model is {aivideo.DEFAULT_VIDEO_MODEL}. The catalogue changes "
                "often — list it here rather than trusting a name that worked last month.",
             muted=True, size=12),
        ft.Row([primary_button(p, "List video models", list_video, ft.Icons.MOVIE_ROUNDED),
                ghost_button(p, "Test key", test_key, ft.Icons.WIFI_TETHERING_ROUNDED),
                ghost_button(p, "Pricing", lambda e: studio.open_url(
                    "https://openrouter.ai/models?fmt=table&output_modalities=video"),
                    ft.Icons.OPEN_IN_NEW_ROUNDED)],
               spacing=10, wrap=True, run_spacing=8),
        ft.Row([status]),
        listing)


def _publishing(studio, s) -> ft.Control:
    p = studio.palette

    youtube_card = _youtube(studio)

    # ---- Google Play
    play_key = text_field(p, "Service account JSON", "", multiline=True,
                          hint="Paste the whole downloaded key file here")

    def save_play(e):
        try:
            email = play.save_service_account(play_key.value)
            snack(studio.page, p, f"Saved. Invite {email} in Play Console.", "ok")
            studio.refresh()
        except Exception as exc:  # noqa: BLE001
            snack(studio.page, p, str(exc), "error")

    play_card = card(
        p,
        ft.Row([h2(p, "Google Play"), pill(p, "bundle, listing, images, rollout", p.apps),
                ft.Container(expand=True)], spacing=8),
        _status_line(p, play.connected(), "Service account saved.", "No service account yet."),
        body(p, "Enable the Google Play Android Developer API, create a service account with a JSON "
                "key, then invite that service account's email as a user in Play Console with "
                "release permissions.", muted=True, size=12),
        play_key,
        ft.Row([primary_button(p, "Save key", save_play, ft.Icons.SAVE_ROUNDED)], spacing=10))

    # ---- App Store
    issuer = text_field(p, "Issuer ID", "")
    key_id = text_field(p, "Key ID", "")
    p8 = text_field(p, ".p8 private key", "", multiline=True,
                    hint="-----BEGIN PRIVATE KEY----- …")

    def save_apple(e):
        try:
            appstore.save_credentials(issuer.value, key_id.value, p8.value)
            snack(studio.page, p, "App Store Connect credentials saved", "ok")
            studio.refresh()
        except Exception as exc:  # noqa: BLE001
            snack(studio.page, p, str(exc), "error")

    apple_card = card(
        p,
        ft.Row([h2(p, "App Store Connect"), pill(p, "metadata, screenshots, submit", p.apps),
                ft.Container(expand=True)], spacing=8),
        _status_line(p, appstore.connected(), "API key saved.", "No API key yet."),
        body(p, "Users and Access → Integrations → App Store Connect API → generate a key with the "
                "App Manager role. Apple shows the .p8 once.", muted=True, size=12),
        ft.Row([ft.Container(issuer, expand=1), ft.Container(key_id, expand=1)], spacing=12),
        p8,
        ft.Row([primary_button(p, "Save", save_apple, ft.Icons.SAVE_ROUNDED)], spacing=10))

    # ---- stock footage and MoneyPrinterTurbo
    pexels = text_field(p, "Pexels API key", keyvault.get_secret(stockvideo.PEXELS_KEY),
                        password=True, helper="Free at pexels.com/api")
    pixabay = text_field(p, "Pixabay API key", keyvault.get_secret(stockvideo.PIXABAY_KEY),
                         password=True, helper="Free at pixabay.com/api/docs")
    stock_status = ft.Text("", size=12, color=p.text_muted, selectable=True, expand=True)

    def _stock_say(text: str, tone: str = "muted") -> None:
        stock_status.value = text
        stock_status.color = {"ok": p.ok, "error": p.danger, "muted": p.text_muted}[tone]
        studio.page.update()

    def save_stock(e):
        stockvideo.save_key("pexels", pexels.value or "")
        stockvideo.save_key("pixabay", pixabay.value or "")
        _stock_say("Stock footage keys saved.", "ok")

    def test_stock(e, source: str):
        stockvideo.save_key("pexels", pexels.value or "")
        stockvideo.save_key("pixabay", pixabay.value or "")
        _stock_say(f"Testing {source}…")
        studio.submit(f"Test {source}", lambda ctx: stockvideo.test_source(source),
                      lambda u: _stock_say(str(u.result) if u.status == "done" else u.message,
                                           "ok" if u.status == "done" else "error"),
                      rerender=False)

    stock_card = card(
        p,
        ft.Row([h2(p, "Stock footage"), pill(p, "video engine", p.video),
                ft.Container(expand=True)], spacing=8),
        _status_line(p, stockvideo.has_key("pexels") or stockvideo.has_key("pixabay"),
                     "A library is configured — the stock video engine is available.",
                     "No stock library configured yet."),
        body(p, "Free clip libraries for the stock-footage video engine. Both keys are free; "
                "Pexels tends to have better footage, Pixabay better coverage of odd topics.",
             muted=True, size=12),
        ft.Row([ft.Container(pexels, expand=1), ft.Container(pixabay, expand=1)], spacing=12),
        ft.Row([primary_button(p, "Save", save_stock, ft.Icons.SAVE_ROUNDED),
                ghost_button(p, "Test Pexels", lambda e: test_stock(e, "pexels"),
                             ft.Icons.WIFI_TETHERING_ROUNDED),
                ghost_button(p, "Test Pixabay", lambda e: test_stock(e, "pixabay"),
                             ft.Icons.WIFI_TETHERING_ROUNDED)], spacing=10, wrap=True,
               run_spacing=8),
        ft.Row([stock_status]))

    mpt_url = text_field(p, "MoneyPrinterTurbo base URL", mpt.base_url(),
                         helper="The server's own address, e.g. http://127.0.0.1:8080")
    mpt_status = ft.Text("", size=12, color=p.text_muted, selectable=True, expand=True)

    def _mpt_say(text: str, tone: str = "muted") -> None:
        mpt_status.value = text
        mpt_status.color = {"ok": p.ok, "error": p.danger, "muted": p.text_muted}[tone]
        studio.page.update()

    def save_mpt(e):
        mpt.save_base_url(mpt_url.value or "")
        _mpt_say(f"Saved. The suite will call {mpt.base_url()}.", "ok")

    def test_mpt(e):
        mpt.save_base_url(mpt_url.value or "")
        _mpt_say("Pinging…")
        studio.submit("Ping MoneyPrinterTurbo", lambda ctx: mpt.ping(),
                      lambda u: _mpt_say(str(u.result) if u.status == "done" else u.message,
                                         "ok" if u.status == "done" else "error"),
                      rerender=False)

    mpt_card = card(
        p,
        ft.Row([h2(p, "MoneyPrinterTurbo"), pill(p, "optional video engine", p.video),
                ft.Container(expand=True)], spacing=8),
        _status_line(p, mpt.configured(), f"Pointing at {mpt.base_url()}",
                     "Not configured — the built-in engines work without it."),
        body(p, "If you already run MoneyPrinterTurbo (harry0703, MIT), the suite can hand it "
                "the script and collect the finished video. It is not bundled: its stack is "
                "heavier than the rest of this app combined, and the built-in stock footage "
                "engine does the same job with only a free Pexels key. To use it: clone the "
                "repo, install its requirements on Python 3.11, then run `python main.py` in "
                "that folder — the API on port 8080, not webui.bat which is a separate "
                "interface on 8501. Confirm it is up at /docs before pressing Ping.",
             muted=True, size=12),
        mpt_url,
        ft.Row([primary_button(p, "Save", save_mpt, ft.Icons.SAVE_ROUNDED),
                ghost_button(p, "Ping server", test_mpt, ft.Icons.WIFI_TETHERING_ROUNDED)],
               spacing=10),
        ft.Row([mpt_status]))

    # ---- local tooling
    tools = card(
        p,
        h2(p, "Local tools"),
        body(p, "These run on your machine, not in the cloud. Missing ones only disable the steps "
                "that need them.", muted=True, size=12),
        _status_line(p, video.available(), "ffmpeg found — video rendering is available.",
                     "ffmpeg not found. Windows: winget install Gyan.FFmpeg · macOS: brew install ffmpeg"),
        _status_line(p, tts.edge_available(), "edge-tts found — free voiceovers are available.",
                     "edge-tts not found. Install with: pip install edge-tts"),
        _status_line(p, browser_mod.available(),
                     "Playwright found — assisted browser publishing is available.",
                     "Playwright not found. pip install playwright && playwright install chromium"))

    return ft.Container(
        content=ft.Column([
            ft.Container(content=body(p, "Connect once. After that the suite publishes without you "
                                         "retyping anything.", muted=True, size=12),
                         padding=ft.padding.only(bottom=2)),
            youtube_card, stock_card, _ai_video(studio), mpt_card, play_card, apple_card,
            tools,
            ft.Container(height=24),
        ], spacing=14, scroll=ft.ScrollMode.AUTO, expand=True),
        padding=ft.padding.only(top=16, right=6), expand=True)


# -------------------------------------------------------------------- general --

def _analytics(studio, s) -> ft.Control:
    """One line: the switch, and a link to what it sends.

    Deliberately not a wall of text. The full field-by-field list lives in
    PRIVACY.md, one click away, which is where anyone who actually wants it will
    look - but the switch itself stays in plain sight, because software that
    reports usage should say so where you can see it and turn it off."""
    p = studio.palette

    def toggle(e):
        s.analytics = e.control.value
        save_settings(s)

    return card(
        p,
        ft.Row([
            ft.Switch(value=s.analytics, active_color=p.accent, on_change=toggle),
            ft.Text("Send anonymous usage data", size=14, color=p.text),
            ft.Container(expand=True),
            ghost_button(p, "What's collected",
                         lambda e: studio.open_url(
                             "https://github.com/anuragstpl/digital-assets-studio"
                             "/blob/main/PRIVACY.md"),
                         ft.Icons.OPEN_IN_NEW_ROUNDED),
        ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER))


def _general(studio, s) -> ft.Control:
    p = studio.palette
    author = text_field(p, "Default author / pen name", s.author_name)
    imprint = text_field(p, "Default imprint or publisher", s.imprint)

    def save(e):
        s.author_name = author.value.strip()
        s.imprint = imprint.value.strip()
        save_settings(s)
        snack(studio.page, p, "Saved", "ok")

    return ft.Container(
        content=ft.Column([
            card(p, h2(p, "Defaults"),
                 body(p, "Used whenever a project does not set its own.", muted=True, size=12),
                 ft.Row([ft.Container(author, expand=1), ft.Container(imprint, expand=1)], spacing=12),
                 primary_button(p, "Save", save, ft.Icons.SAVE_ROUNDED)),
            card(p, h2(p, "Appearance"),
                 ft.Row([ft.Switch(value=s.dark_mode, active_color=p.accent,
                                   on_change=lambda e: studio.set_dark(e.control.value)),
                         ft.Text("Dark mode", size=14, color=p.text)], spacing=10)),
            card(p, h2(p, "Workspace"),
                 body(p, str(studio.workspace), muted=True, size=12, selectable=True),
                 ft.Row([ghost_button(p, "Open workspace folder",
                                      lambda e: studio.reveal(str(studio.workspace)),
                                      ft.Icons.FOLDER_OPEN_ROUNDED)], spacing=10)),
            _analytics(studio, s),
            ft.Container(height=24),
        ], spacing=14, scroll=ft.ScrollMode.AUTO, expand=True),
        padding=ft.padding.only(top=16, right=6), expand=True)
