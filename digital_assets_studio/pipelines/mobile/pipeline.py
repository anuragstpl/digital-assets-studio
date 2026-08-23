"""The mobile app pipeline: store listing to live release."""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from ...config import ROLE_MARKETING, ROLE_METADATA, ROLE_PLANNING
from ...core.jobs import JobContext
from ...core.llm import router
from ...core.pipeline import (AUTO, EXTERNAL, MANUAL, REVIEW, Field_, Link, Pipeline,
                              Step, StepResult)
from ...core.projects import Project
from ...core.publishing import appstore, play
from .assets import (IOS_6_7, PLAY_PHONE, ShotSpec, feature_graphic, frame_screenshot,
                     placeholder_screenshot, save)

ASO_NOTE = """You write app store listings that survive both the store's search
ranking and its review team. Never claim a feature the app does not have, never
use a competitor's trademark, never promise medical, financial or legal outcomes."""


def _json(p: Project, name: str) -> dict:
    raw = p.read_text(f"drafts/{name}", "")
    return json.loads(raw) if raw else {}


def _step_positioning(p: Project, ctx: JobContext) -> StepResult:
    ctx.progress(0.3, "Positioning the app")
    prompt = f"""Position this app for the stores.

App: {p.answer('app_name')}
What it does: {p.answer('what_it_does')}
Who for: {p.answer('audience')}
Platforms: {p.answer('platforms')}
Business model: {p.answer('model')}

Return JSON:
  one_liner        - what it is, in one sentence a stranger understands
  primary_value    - the single job the app is hired to do
  competitors      - array of 3 real competing apps and, for each, one thing they do badly
  search_terms     - array of 15 phrases users would search in the store, most valuable first
  differentiators  - array of 4
  objections       - array of 3 reasons someone would uninstall in week one, and the fix for each
  risky_claims     - array of anything in the above that a store reviewer could challenge
"""
    data = router.text_json(ROLE_PLANNING, prompt, ASO_NOTE, max_tokens=3000)
    p.write_text("drafts/positioning.json", json.dumps(data, indent=2))
    md = ["# Positioning", "", f"**One-liner** — {data.get('one_liner','')}", "",
          f"**Primary value** — {data.get('primary_value','')}", "", "## Search terms", ""]
    md += [f"- {t}" for t in data.get("search_terms", [])]
    md += ["", "## Differentiators", ""] + [f"- {d}" for d in data.get("differentiators", [])]
    md += ["", "## Week-one objections", ""] + [f"- {o}" for o in data.get("objections", [])]
    md += ["", "## Claims a reviewer might challenge", ""] + [f"- {r}" for r in data.get("risky_claims", [])]
    p.write_text("drafts/positioning.md", "\n".join(md))
    return StepResult("Positioning and search terms ready", ["drafts/positioning.md"])


def _step_listing(p: Project, ctx: JobContext) -> StepResult:
    pos = _json(p, "positioning.json")
    ctx.progress(0.4, "Writing the store listings")
    prompt = f"""Write the store listings for this app. Respect every character limit exactly.

App: {p.answer('app_name')}
One-liner: {pos.get('one_liner','')}
Value: {pos.get('primary_value','')}
Search terms: {json.dumps(pos.get('search_terms', []))}
Model: {p.answer('model')}

Return JSON:
  play_title            - max 30 characters
  play_short            - max 80 characters
  play_full             - max 4000 characters, scannable, no keyword stuffing
  ios_name              - max 30 characters
  ios_subtitle          - max 30 characters
  ios_keywords          - max 100 characters TOTAL, comma separated, no spaces after commas,
                          no words already in the name or subtitle, singular forms only
  ios_description       - max 4000 characters
  ios_promotional_text  - max 170 characters
  whats_new             - max 500 characters
  screenshot_captions   - array of 6 captions, each under 45 characters, one per screenshot
  screenshot_subcaptions- array of 6 supporting lines, each under 70 characters
"""
    data = router.text_json(ROLE_MARKETING, prompt, ASO_NOTE, max_tokens=6000)
    p.write_text("drafts/listing.json", json.dumps(data, indent=2))

    problems = []
    for key, cap in (("play_title", 30), ("play_short", 80), ("play_full", 4000),
                     ("ios_name", 30), ("ios_subtitle", 30), ("ios_keywords", 100),
                     ("ios_description", 4000), ("ios_promotional_text", 170), ("whats_new", 500)):
        val = data.get(key, "") or ""
        if len(val) > cap:
            problems.append(f"{key} is {len(val)} chars, limit {cap}")
            data[key] = val[:cap]
    if problems:
        p.write_text("drafts/listing.json", json.dumps(data, indent=2))

    md = ["# Store listings", "", "## Google Play", "",
          f"**Title ({len(data.get('play_title',''))}/30)** — {data.get('play_title','')}", "",
          f"**Short ({len(data.get('play_short',''))}/80)** — {data.get('play_short','')}", "",
          "**Full description**", "", "```", data.get("play_full", ""), "```", "",
          "## App Store", "",
          f"**Name** — {data.get('ios_name','')}", "",
          f"**Subtitle** — {data.get('ios_subtitle','')}", "",
          f"**Keywords ({len(data.get('ios_keywords',''))}/100)** — `{data.get('ios_keywords','')}`", "",
          "**Description**", "", "```", data.get("ios_description", ""), "```", "",
          f"**Promotional text** — {data.get('ios_promotional_text','')}", "",
          f"**What's new** — {data.get('whats_new','')}", ""]
    p.write_text("drafts/listing.md", "\n".join(md))
    note = "Listings written" + (f" — trimmed {len(problems)} over-length field(s)" if problems else "")
    return StepResult(note, ["drafts/listing.md", "drafts/listing.json"])


def _step_privacy(p: Project, ctx: JobContext) -> StepResult:
    ctx.progress(0.4, "Drafting the privacy policy and data-safety answers")
    prompt = f"""Produce the privacy documentation for this app.

App: {p.answer('app_name')} ({p.answer('package')})
Publisher: {p.answer('publisher')}
Contact email: {p.answer('contact_email')}
What it does: {p.answer('what_it_does')}
Data it handles: {p.answer('data_collected')}
Third parties: {p.answer('third_parties')}
Ads: {p.answer('ads')}
Analytics: {p.answer('analytics')}
Account deletion URL: {p.answer('delete_url')}

Return JSON:
  privacy_policy_markdown - a complete policy. Sections: what we collect, why, legal basis,
                            sharing, third parties named, retention, security, children,
                            your rights, account and data deletion (state the URL), changes,
                            contact. Concrete, no boilerplate about "your privacy is important".
  play_data_safety        - array of objects {{data_type, collected, shared, purpose,
                            optional, ephemeral, encrypted_in_transit, deletable}} covering
                            exactly the data listed above and nothing else
  ios_privacy_nutrition   - array of objects {{data_type, linked_to_user, used_for_tracking, purpose}}
  gaps                    - array of anything the developer must confirm before submitting,
                            phrased as a direct question
"""
    data = router.text_json(ROLE_MARKETING, prompt, ASO_NOTE, max_tokens=8000)
    p.write_text("drafts/privacy.json", json.dumps(data, indent=2))
    p.write_text("drafts/privacy_policy.md", data.get("privacy_policy_markdown", ""))

    rows = ["| Data type | Collected | Shared | Purpose | Optional | Encrypted | Deletable |",
            "|---|---|---|---|---|---|---|"]
    for d in data.get("play_data_safety", []):
        rows.append("| {data_type} | {collected} | {shared} | {purpose} | {optional} | "
                    "{encrypted_in_transit} | {deletable} |".format(**{
                        **{k: "" for k in ("data_type", "collected", "shared", "purpose",
                                           "optional", "encrypted_in_transit", "deletable")}, **d}))
    ios = ["| Data type | Linked to user | Used for tracking | Purpose |", "|---|---|---|---|"]
    for d in data.get("ios_privacy_nutrition", []):
        ios.append("| {data_type} | {linked_to_user} | {used_for_tracking} | {purpose} |".format(**{
            **{k: "" for k in ("data_type", "linked_to_user", "used_for_tracking", "purpose")}, **d}))
    md = ["# Data safety answer sheet", "",
          "Copy these into Play Console › Data safety and App Store Connect › App Privacy. "
          "Neither form has an API — this is the sheet that makes them a five-minute job.", "",
          "## Google Play", "", "\n".join(rows), "", "## App Store", "", "\n".join(ios), ""]
    if data.get("gaps"):
        md += ["## Confirm these before submitting", ""] + [f"- {g}" for g in data["gaps"]]
    p.write_text("drafts/data_safety.md", "\n".join(md))
    return StepResult("Privacy policy and both data-safety answer sheets written",
                      ["drafts/privacy_policy.md", "drafts/data_safety.md"])


def _shot_spec(p: Project, caption: str = "", sub: str = "") -> ShotSpec:
    return ShotSpec(caption=caption, subcaption=sub,
                    bg=p.answer("brand_bg", "#0E1117"), bg2=p.answer("brand_bg2", "#243B6B"),
                    accent=p.answer("brand_accent", "#3B5BDB"))


def _step_screenshots(p: Project, ctx: JobContext) -> StepResult:
    listing = _json(p, "listing.json")
    caps = listing.get("screenshot_captions", []) or ["Feature one"]
    subs = listing.get("screenshot_subcaptions", []) or [""]
    folder = Path(p.answer("screenshot_folder", "")).expanduser() if p.answer("screenshot_folder") else None

    raws: list[Image.Image] = []
    if folder and folder.is_dir():
        for f in sorted(folder.iterdir()):
            if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                raws.append(Image.open(f))
        ctx.log(f"Found {len(raws)} raw screenshots in {folder}")
    if not raws:
        ctx.log("No raw screenshots supplied — using neutral placeholders so the rest of the "
                "pipeline can run. Point the step at your screenshot folder to use real ones.")
        raws = [placeholder_screenshot(i) for i in range(1, len(caps) + 1)]

    made: list[str] = []
    for i, raw in enumerate(raws[:8]):
        ctx.progress(i / max(len(raws[:8]), 1), f"Framing screenshot {i + 1}")
        spec = _shot_spec(p, caps[i % len(caps)], subs[i % len(subs)] if subs else "")
        rel = f"build/screenshots/play/{i + 1:02d}.png"
        save(frame_screenshot(raw, spec, PLAY_PHONE), p.dir / rel)
        made.append(rel)
        rel_ios = f"build/screenshots/ios/{i + 1:02d}.png"
        save(frame_screenshot(raw, spec, IOS_6_7), p.dir / rel_ios)
        made.append(rel_ios)

    fg = feature_graphic(p.answer("app_name"), listing.get("play_short", ""), _shot_spec(p))
    save(fg, p.build / "feature_graphic.png")
    made.append("build/feature_graphic.png")
    return StepResult(f"{len(raws[:8])} screenshots framed for both stores, plus the feature graphic", made)


def _step_play_publish(p: Project, ctx: JobContext) -> StepResult:
    listing = _json(p, "listing.json")
    package = p.answer("package")
    if not package:
        raise RuntimeError("Set the package name (e.g. com.example.app) in the project fields first.")
    aab = p.answer("aab_path", "")
    aab_path = Path(aab).expanduser() if aab else None
    if aab_path and not aab_path.exists():
        raise RuntimeError(f"No bundle at {aab_path}. Build it first, or clear the field to publish "
                           f"listing text and images only.")

    shots = sorted((p.dir / "build" / "screenshots" / "play").glob("*.png"))
    images = {"phoneScreenshots": shots[:8]}
    fg = p.build / "feature_graphic.png"
    if fg.exists():
        images["featureGraphic"] = [fg]
    icon = Path(p.answer("icon_path", "")).expanduser() if p.answer("icon_path") else None
    if icon and icon.exists():
        images["icon"] = [icon]

    out = play.publish(
        package=package,
        aab=aab_path,
        listing={"title": listing.get("play_title", p.answer("app_name"))[:30],
                 "short_description": listing.get("play_short", "")[:80],
                 "full_description": listing.get("play_full", "")[:4000]},
        images=images,
        track=p.answer("play_track", "internal"),
        release_notes={"en-US": listing.get("whats_new", "")[:500]} if listing.get("whats_new") else None,
        rollout=float(p.answer("rollout", 0) or 0) or None,
        progress=lambda f, m: ctx.progress(f, m),
    )
    p.write_text("build/play_release.json", json.dumps(out, indent=2, default=str))
    vc = out.get("version_code")
    return StepResult(
        f"Committed to Play on the {p.answer('play_track', 'internal')} track"
        + (f" — version code {vc}" if vc else " — listing and images only"),
        ["build/play_release.json"])


def _step_appstore_push(p: Project, ctx: JobContext) -> StepResult:
    listing = _json(p, "listing.json")
    app_id = p.answer("appstore_app_id")
    if not app_id:
        apps = appstore.list_apps()
        match = next((a for a in apps if a["bundle_id"] == p.answer("bundle_id")), None)
        if not match:
            raise RuntimeError("Set the App Store app ID, or the bundle ID so I can find it. "
                               f"Visible apps: {', '.join(a['bundle_id'] for a in apps) or 'none'}")
        app_id = match["id"]
    version = p.answer("version_string", "1.0.0")
    ctx.progress(0.3, "Finding or creating the version")
    existing = appstore.latest_version(app_id)
    if existing and existing["attributes"].get("versionString") == version:
        version_id = existing["id"]
    else:
        version_id = appstore.create_version(app_id, version)["data"]["id"]
    ctx.progress(0.6, "Writing the localisation")
    appstore.update_localization(
        version_id, "en-US",
        description=listing.get("ios_description", ""),
        keywords=listing.get("ios_keywords", ""),
        whats_new=listing.get("whats_new", ""),
        promotional_text=listing.get("ios_promotional_text", ""),
        support_url=p.answer("support_url", ""),
        marketing_url=p.answer("marketing_url", ""))
    p.write_text("build/appstore_version.json", json.dumps({"app_id": app_id, "version_id": version_id}, indent=2))
    return StepResult(f"App Store metadata written for {version}. "
                      f"Upload the build with Transporter, then submit for review.",
                      ["build/appstore_version.json"], {"appstore_version_id": version_id})


MOBILE_PIPELINE = Pipeline(
    id="mobile",
    title="Mobile app",
    subtitle="Google Play and the App Store",
    description=("Listing copy inside every character limit, a privacy policy, data-safety "
                 "answer sheets for both stores, framed screenshots, and a real API release "
                 "to Play — bundle, listing, images and rollout in one commit."),
    icon="phone_iphone_rounded",
    accent="apps",
    intake=[
        Field_("app_name", "App name", required=True),
        Field_("package", "Android package name", placeholder="com.example.app"),
        Field_("bundle_id", "iOS bundle ID", placeholder="com.example.app"),
        Field_("what_it_does", "What the app does", "multiline", required=True),
        Field_("audience", "Who it is for", "multiline", required=True),
        Field_("platforms", "Platforms", "select",
               options=["Android and iOS", "Android only", "iOS only"], default="Android and iOS"),
        Field_("model", "Business model", "select",
               options=["Free", "Free with ads", "Freemium with a paid tier",
                        "Paid up front", "Subscription"], default="Freemium with a paid tier"),
        Field_("publisher", "Publisher / company name"),
        Field_("contact_email", "Support email"),
    ],
    steps=[
        Step("positioning", "Position the app", "Prepare", AUTO, run=_step_positioning,
             summary="One-liner, competitors, and the search terms the listing has to win.",
             produces=["drafts/positioning.md"], run_label="Work out positioning"),
        Step("listing", "Write both store listings", "Prepare", AUTO, run=_step_listing,
             requires=["positioning"],
             summary="Play and App Store copy, every field checked against its character limit.",
             produces=["drafts/listing.md"], run_label="Write listings"),
        Step("privacy", "Privacy policy and data safety", "Prepare", AUTO, run=_step_privacy,
             requires=["positioning"],
             summary="A real policy plus filled-in answer sheets for both stores' privacy forms.",
             fields=[
                 Field_("data_collected", "What data the app handles", "multiline", required=True,
                        placeholder="name, email, phone, photos, contacts, approximate location, app usage"),
                 Field_("third_parties", "Third parties", "multiline",
                        placeholder="Firebase Analytics, AdMob, Vercel Blob, Nominatim geocoding"),
                 Field_("ads", "Ads", "select", options=["No ads", "Ads, shipping now", "Ads, behind a flag"],
                        default="No ads"),
                 Field_("analytics", "Analytics", "select",
                        options=["None", "Firebase Analytics", "Crashlytics only", "Other"], default="None"),
                 Field_("delete_url", "Account deletion URL",
                        help="Play requires a working URL that deletes the account AND its data."),
             ],
             produces=["drafts/privacy_policy.md", "drafts/data_safety.md"],
             run_label="Draft privacy pack"),
        Step("host_policy", "Publish the privacy policy", "Prepare", MANUAL, requires=["privacy"],
             summary="Both stores reject a policy URL that 404s or points at a login wall.",
             instructions=("Put `drafts/privacy_policy.md` on a public URL — your own site, a GitHub "
                           "Pages file, anything that loads without signing in.\n\n"
                           "Check three things after publishing:\n"
                           "1. It opens in a private browsing window.\n"
                           "2. The account-deletion URL in it actually works and deletes data, not just the login.\n"
                           "3. It names the app by the exact name in the listing."),
             fields=[Field_("privacy_url", "Live privacy policy URL", required=True)],
             checklist=["Policy is public", "Deletion URL works", "App named correctly"]),

        Step("screenshots", "Frame the store screenshots", "Assets", AUTO, run=_step_screenshots,
             requires=["listing"],
             summary="Your raw screens on branded backgrounds with captions, sized for both stores.",
             fields=[
                 Field_("screenshot_folder", "Folder of raw screenshots",
                        help="Leave blank to render neutral placeholders you can replace later."),
                 Field_("brand_accent", "Accent colour", default="#3B5BDB"),
                 Field_("brand_bg", "Background colour", default="#0E1117"),
                 Field_("brand_bg2", "Background colour 2", default="#243B6B"),
             ],
             produces=["build/screenshots", "build/feature_graphic.png"], run_label="Frame screenshots"),
        Step("policy_review", "Policy landmine check", "Assets", MANUAL, requires=["listing"],
             gate=REVIEW,
             summary="The five things that get apps rejected, in the order they get caught.",
             instructions=(
                 "1. **Payments.** Selling digital goods or a subscription through your own card form "
                 "breaches Play's Payments policy and Apple's. Use Play Billing / StoreKit, or gate the "
                 "purchase flow behind a compile-time flag that ships off.\n"
                 "2. **Ads and the advertising ID.** If ads ship disabled, the `AD_ID` permission must be "
                 "removed from the manifest and Data safety must not declare device IDs. When you turn ads "
                 "on, all three change together.\n"
                 "3. **Account deletion.** Play requires an in-app path *and* a public web URL, and it must "
                 "delete the data, not just the account row.\n"
                 "4. **Target API level.** Play blocks updates below the current requirement. Check it before "
                 "you build, not after the upload fails.\n"
                 "5. **Permissions.** Every dangerous permission needs a visible in-app reason. Location and "
                 "contacts are the two reviewers actually check."),
             checklist=["No custom payment form for digital goods", "AD_ID matches the ads reality",
                        "Deletion works end to end", "Target API level current",
                        "Every permission justified in-app"],
             links=[Link("Play Payments policy", "https://support.google.com/googleplay/android-developer/answer/9858738"),
                    Link("Target API requirements", "https://developer.android.com/google/play/requirements/target-sdk")]),

        Step("play_connect", "Connect Google Play", "Ship", MANUAL, optional=True,
             summary="One-time. After this, releases are one click from inside the suite.",
             instructions=(
                 "1. Google Cloud console → the project linked to your Play account → "
                 "**Enable the Google Play Android Developer API**.\n"
                 "2. IAM → Service accounts → create one → Keys → **Add key → JSON**. Download it.\n"
                 "3. Play Console → Users and permissions → **Invite user** → paste the service "
                 "account's email → grant *Release to production, exclude devices, and use Play App "
                 "Signing* plus *Edit and delete draft apps* on this app.\n"
                 "4. Paste the JSON key into **Settings › Publishing › Google Play**.\n\n"
                 "Note: a brand-new app needs its very first release uploaded through the console by "
                 "hand — Google does not allow the API to create the first one. Every release after "
                 "that is automated."),
             checklist=["API enabled", "Service account key downloaded",
                        "Service account invited in Play Console", "Key pasted into Settings"],
             links=[Link("Play Console users", "https://play.google.com/console"),
                    Link("Google Cloud console", "https://console.cloud.google.com/")]),
        Step("play_publish", "Publish to Google Play", "Ship", AUTO, run=_step_play_publish,
             requires=["screenshots", "host_policy"],
             summary="Uploads the bundle, writes the listing, replaces the images and creates the "
                     "release — one atomic edit, committed only if every part succeeds.",
             fields=[
                 Field_("aab_path", "Path to the .aab", help="Leave blank to push listing and images only."),
                 Field_("icon_path", "Path to the 512×512 icon"),
                 Field_("play_track", "Track", "select",
                        options=["internal", "alpha", "beta", "production"], default="internal"),
                 Field_("rollout", "Staged rollout fraction", "number", default=0,
                        help="0 for a full release, or 0.1 for 10%."),
             ],
             produces=["build/play_release.json"], run_label="Publish to Play"),
        Step("play_forms", "Finish the two console-only forms", "Ship", MANUAL, requires=["play_publish"],
             summary="Data safety and content rating have no API. The answers are already written.",
             instructions=("Open `drafts/data_safety.md` — every answer is in the table.\n\n"
                           "**Data safety** — Play Console › App content › Data safety. Answer exactly "
                           "what the table says. Over-declaring gets you a higher age rating; "
                           "under-declaring gets the app pulled.\n\n"
                           "**Content rating** — App content › Content rating. Answer the questionnaire "
                           "honestly. Declaring user-generated content raises the rating in some regions "
                           "(Germany's USK in particular) — that is correct, not a mistake to work around.\n\n"
                           "**App access** — if any feature is behind a login, give the reviewer working "
                           "demo credentials or the review fails without ever opening the app."),
             checklist=["Data safety submitted", "Content rating submitted",
                        "App access / demo login provided", "Ads declaration matches reality"],
             links=[Link("Play Console", "https://play.google.com/console")]),
        Step("appstore_connect", "Connect App Store Connect", "Ship", MANUAL, optional=True,
             summary="One-time. Metadata and screenshots then push by API.",
             instructions=("App Store Connect → Users and Access → Integrations → **App Store Connect API** "
                           "→ generate a key with the *App Manager* role. Download the `.p8` once — Apple "
                           "will not show it again.\n\n"
                           "Paste the Issuer ID, Key ID and the `.p8` contents into "
                           "**Settings › Publishing › App Store**."),
             checklist=["API key generated", "Issuer ID, Key ID and .p8 saved in Settings"],
             links=[Link("App Store Connect keys", "https://appstoreconnect.apple.com/access/integrations/api")]),
        Step("appstore_push", "Push App Store metadata", "Ship", AUTO, run=_step_appstore_push,
             requires=["screenshots", "host_policy"], optional=True,
             summary="Creates the version and writes description, keywords, promo text and release notes.",
             fields=[Field_("appstore_app_id", "App Store app ID", help="Blank looks it up by bundle ID."),
                     Field_("version_string", "Version", default="1.0.0"),
                     Field_("support_url", "Support URL"), Field_("marketing_url", "Marketing URL")],
             run_label="Push to App Store Connect"),
        Step("ios_build", "Upload the iOS build", "Ship", MANUAL, requires=["appstore_push"], optional=True,
             summary="The one step Apple gives no API for — it needs macOS.",
             instructions=("Apple accepts binaries only through Transporter or `altool`, both macOS-only:\n\n"
                           "```bash\n"
                           "xcrun altool --upload-app -f build/YourApp.ipa -t ios \\\n"
                           "  -u \"$APPLE_ID\" -p \"$APP_SPECIFIC_PASSWORD\"\n"
                           "```\n\n"
                           "Generate the app-specific password at appleid.apple.com, never your real one. "
                           "Processing takes 10–30 minutes; the build appears in App Store Connect after that, "
                           "and only then can you attach it to the version and submit."),
             checklist=["IPA uploaded", "Build finished processing", "Build attached to the version",
                        "Submitted for review"],
             links=[Link("Apple ID app passwords", "https://appleid.apple.com/account/manage")]),
        Step("post_launch", "After the release", "Ship", MANUAL, optional=True,
             summary="Where the money actually comes from after day one.",
             instructions=("- **Watch the vitals.** Play's Android vitals and crash rate decide whether "
                           "the store keeps showing your app. A bad ANR rate suppresses it silently.\n"
                           "- **Reply to every review** for the first month. Reply rate correlates with "
                           "rating recovery more than anything else you can do.\n"
                           "- **Ship a small update within three weeks.** Store ranking rewards active apps.\n"
                           "- **Instrument one funnel**, not twenty events: install → first value moment → "
                           "return on day two. If day-two return is under 20%, no amount of ASO will help.\n"
                           "- If you monetise, turn purchases on only after the funnel holds."),
             checklist=["Vitals dashboard checked", "Reviews answered", "Day-2 retention measured",
                        "Next update scheduled"]),
    ],
)
