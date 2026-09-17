# Live inspection of https://runpod.galaxygate.app

Inspected 2026-09-17 against the public site behind Cloudflare, driven in a real
Chrome via agent-browser. Walked the full attendee path at 390x844, 360x780 and
1440x900, plus a 320x568 stress pass. Every screenshot in this directory was taken
during that run.

Counts: 2 blockers, 3 majors, 8 minors, 6 nits.

## The headline check: what an attendee sees with an empty pool

Confirmed. Submitting an email today lands on `/claim` showing "You are on the list",
not a credit link and not an error. Verified at 390 (`390-result-on-the-list.png`),
at 360 (`360-result-on-the-list.png`) and at 1440 (`1440-result-on-the-list.png`).
The heading and the first line are genuinely reassuring. The two sentences under them
are not, and both are blockers below.

## Blockers

### B1. The page promises an email the software cannot send

What I did. Walked to the end at 390 and read the result page.

What I saw. "Yours will be emailed to you." I then grepped the whole application for
any mail path. `grep -rni 'smtp|sendgrid|mailgun|send_mail|ses' app/ requirements.txt`
returns nothing. `app/emails.py` only normalizes addresses and truncates IPs. There is
no scheduler, no queue, no outbound mail of any kind. The only route from a stored
entry to a human is an operator pulling `/admin/entries.csv` and mailing people by hand.

Screenshot: `390-result-on-the-list.png`.

Why it blocks. Every attendee sees this sentence today. It commits the team to an
action the product does not perform and does not remind anyone to perform. People will
wait for an email that no code sends.

Fix. Change `app/templates/no_link.html` to describe the real mechanism and name a
time and a place, for example: "Your link is not ready yet. Check back at this page
later today, or find us at the GalaxyGate table and we will hand it to you." Only say
"emailed" once something actually sends it.

### B2. "Every credit link on hand is taken right now" reads as you missed out

What I did. Same walk, read the same paragraph as an attendee would.

What I saw. "Every credit link on hand is taken right now." The pool is empty because
the links have not arrived, not because other people claimed them. The sentence says
the opposite. An attendee reads it as other people were faster and there is nothing
left for them.

Screenshot: `390-result-on-the-list.png`, second paragraph.

Fix. State the actual situation with no scarcity framing. "The credit links are not
loaded yet. Your spot is saved and yours is reserved." Drop "taken" entirely.

## Majors

### M1. Typing or revisiting /email or /claim gives a raw JSON error page

What I did. Opened `https://runpod.galaxygate.app/claim` and
`https://runpod.galaxygate.app/email` directly in a clean browser session with no
prior cookies, exactly as someone would from browser history or a shared link.

What I saw. A bare white page with a "Pretty-print" checkbox and the text
`{"detail":"Method Not Allowed"}`. No branding, no explanation, no link home.
`curl` confirms HTTP 405 with `content-type: application/json` on both paths.
There is no stack trace, so nothing leaks, but nothing helps either.

Screenshots: `390-direct-claim-no-session.png`, `390-direct-email-no-session.png`.

Why it matters. This is the likely path back for an attendee who wants to re-open the
page later or show a friend. It is also what the organizers will hit if anyone pastes
the wrong URL on a slide.

Fix. Add `GET` handlers for `/email` and `/claim` in `app/main.py` that render a
branded page saying the form has to be started from the beginning, with a link to `/`.
Add a FastAPI exception handler for 404 and 405 that returns the same branded page
instead of JSON.

### M2. Reloading the result page re-submits the form

What I did. Finished the walk, then reloaded `/claim`, with the network log cleared
first.

What I saw. The reload issued `POST https://runpod.galaxygate.app/claim (Document) 200`.
The page re-rendered identically. The same happens on step 2: reloading `/email`
re-issues `POST /email`. There is no post-then-redirect anywhere in the flow.
Chrome in this harness auto-confirms the resubmission. A real phone browser puts a
"Confirm Form Resubmission" interstitial in front of the attendee instead.

Screenshots: `390-result-after-reload.png`, `390-step2-after-reload-reposts.png`.

Why it matters. Once the real links are loaded, a reload re-enters the claim path.
The code survives it (`entries.normalized_email` is UNIQUE and `db.claim` runs inside
`BEGIN IMMEDIATE`, so the same person gets the same link back), but the attendee is
shown a browser warning page at the exact moment they are trying to read their credit.

Fix. Redirect after the POST. Store the entry, set a short signed cookie carrying the
normalized email, then `303` to a `GET /claim` that renders from the cookie. That also
fixes M1 for `/claim`.

### M3. The result page never shows which email was recorded

What I did. Submitted `attendee390@example.invalid` and read the confirmation.

What I saw. The page says "Your raffle spot is recorded" and never repeats the
address. There is no way to check for a typo and no link to correct one.

Screenshot: `390-result-on-the-list.png`.

Why it matters. It compounds B1. The attendee is told an email is coming, is not shown
where it is going, and cannot fix it if they fat-fingered it on a phone keyboard.

Fix. Echo the address in `no_link.html` and `result.html`, for example
"We have you as attendee390@example.invalid", with a link back to `/` labelled
"That is wrong, start over".

## Minors

### m1. Browser back after submitting shows a form that looks unsubmitted

What I did. Submitted successfully, then pressed browser back.

What I saw. Step 2 with the email still typed in, the Submit button live, and nothing
saying the signup already went through. Screenshot: `390-back-after-submit.png`.
Pressing Submit again returns the same "You are on the list" page, so no damage is
done, but the attendee cannot tell they are already registered.

Fix. Send `Cache-Control: no-store` on the step 2 response so back re-fetches, and
have the server recognise an already-registered address and say so.

### m2. The "Back" link on step 2 throws away the typed Discord username

What I did. Typed `attendee360` on step 1 at 360 wide, went to step 2, clicked "Back".

What I saw. Step 1 with the Discord username field empty. The value is gone.
Screenshot: `360-back-link-loses-username.png`.

Fix. Make "Back" a POST to `/` carrying `discord_username`, or render it as
`<a href="/?discord=...">` and prefill `step1.html` from the query string.

### m3. The "Back" link is a 33 by 23 pixel tap target

What I did. Measured every control's bounding box at 390 wide.

What I saw. The Discord button, the text input and Submit are all 320 by 56, which is
comfortable. "Back" is 33 by 23, well under the 44 by 44 guideline, and it sits close
to the Submit button. Screenshot: `390-step2-email.png`.

Fix. Give `.card a.back` `display:inline-block; min-height:44px; padding:12px 8px;` in
`app/static/style.css`.

### m4. The card is pinned to the top, leaving a large empty void

What I did. Looked at every full-page screenshot.

What I saw. At 390 the card ends around y=630 of an 844 tall screen. At 1440 the card
ends at y=370 of 900, leaving over 500 pixels of flat black. On the short result page
the void is more than half the screen and reads as a page that failed to finish
loading. Screenshots: `1440-result-on-the-list.png`, `360-result-on-the-list.png`.

Cause. `body { align-items: flex-start }` at line 31 of `app/static/style.css`.

Fix. Change it to `align-items: center`. Keep the 20px top padding as a minimum so
tall content still starts at the top on small screens.

### m5. Neither input has a length limit

What I did. Typed a 300 character Discord username, and separately a 256 character
email address, and submitted both.

What I saw. Both were accepted and stored. The 300 character username renders as a
ten line block inside the step 2 callout, which pushes the email field and Submit
below the fold but does not overflow horizontally. The 256 character email produced a
normal result page. Screenshots: `390-step2-300char-discord-overflow.png`,
`390-long-email-accepted-result.png`.

A 467 character address was blocked by the browser's own email validation with
"Please enter an email address", never reaching the server
(`390-long-email-result.png`).

Fix. Add `maxlength="64"` to the Discord input and `maxlength="254"` to the email
input, and enforce the same limits in `submit()` in `app/main.py` so a direct POST
cannot bypass them.

### m6. Submit gives no feedback while the request is in flight

What I did. Checked the served markup of step 1 and step 2.

What I saw. Zero `<script>` tags on either page. The Submit button has no disabled
state and no spinner. On the venue wifi a slow POST looks to the attendee like the tap
did not register, and they will tap again.

Fix. Add a six line inline script that disables the button and swaps its label to
"Saving" on submit. Keep the form working without it.

### m7. The page is dark only and ignores a light-mode phone

What I did. Set the browser to `prefers-color-scheme: light` and reloaded step 1.

What I saw. A byte-identical render to the dark pass, confirmed by matching md5sums of
`390-step1-light-mode-ignored.png` and `390-step1-full.png`. `<meta name="color-scheme"
content="dark">` forces it and the stylesheet has no `prefers-color-scheme` rule.

On contrast itself the page is fine. Measured ratios against the actual painted
background: body text 9.63:1, headings 15.97:1, the green Submit label 11.09:1, the
Discord button 4.61:1. All pass AA. The bright room risk is the dark ground, not the
text.

Fix. Either accept it as a deliberate choice, or add a light palette under
`@media (prefers-color-scheme: light)` and drop the forcing meta tag.

### m8. Plus-alias folding applies to every domain, not just Gmail

What I did. Read `normalize_email` in `app/emails.py` after seeing the one-link-per
-person rule in the schema.

What I saw. Everything from the first `+` to the `@` is stripped for all domains.
`bob+2@company.com` and `bob@company.com` collapse to the same identity. Today, with an
empty pool, this only merges raffle entries. Once the links are loaded, the second
person to submit a plus-alias of an address already in the table is shown the first
person's single-use credit link.

Fix. Restrict the plus-stripping to the Gmail domains, the same way the dot-stripping
is already restricted.

## Nits

- **No question mark on the step 2 heading.** "Where should the credit go" is phrased
  as a question. Seen in `390-step2-email.png`. Add the question mark.
- **No meta description and no Open Graph tags.** The `<head>` carries only charset,
  viewport, color-scheme, title and the stylesheet. A link shared in Discord previews
  as a bare URL. Add `description`, `og:title`, `og:description`.
- **No security headers on any response.** `curl -I` shows no HSTS, no
  `X-Content-Type-Options`, no `Referrer-Policy`, no CSP. `http://` does 301 to
  `https://` correctly, but with no HSTS the first hop is in the clear. Add them at
  the Cloudflare edge.
- **`/health` is public and unauthenticated.** It returns
  `{"status":"ok","links_total":0,"links_remaining":0,"discord_invite_set":true,"admin_configured":true}`
  to anyone. It tells a stranger how many credits are left and that an admin surface is
  live. Gate it or trim it to `{"status":"ok"}`.
- **The placeholder is the browser default gray.** `rgb(117,117,117)` at 4.07:1 while
  every other colour on the page is deliberate. Style `::placeholder` with `--muted`
  dimmed.
- **The URL says `/claim` while the page shows step 2** after a server-side email
  error. Seen in `390-server-side-email-error.png`. The redirect in M2 fixes this too.

## Things I tried to break that held up

- **Empty email.** Blocked by the browser with "Please fill out this field", no request
  sent. `390-empty-email-validation.png`.
- **Email with no at sign.** Blocked with "Please include an '@' in the email address".
  `390-no-at-sign-validation.png`. I also stripped the `type="email"` attribute to reach
  the server directly, and the server rejected it too, re-rendering step 2 with a red
  `role="alert"` callout and the typed value preserved. `390-server-side-email-error.png`.
- **Double tapping Submit.** One `POST /claim` in the network log, one result page.
  `390-double-tap-submit.png`. The schema backs this up with a unique index on
  `normalized_email` and a `BEGIN IMMEDIATE` transaction around the claim.
- **Horizontal scroll.** None at any width. `scrollWidth` equals `clientWidth` at 320,
  360, 390 and 1440. The 300 character username wraps inside its callout rather than
  pushing the layout wide.
- **Step indicator.** Correct on every page. Step 1 marks Discord current, step 2 marks
  Discord done and Email current, the result marks all three done with Credit current.
- **The Discord button.** Points at `https://discord.gg/runpod` with
  `target="_blank" rel="noopener noreferrer"`. Resolved the invite through Discord's
  API without joining: guild "Runpod", id `912829806415085598`, no expiry. Correct
  target, and it opens in a new tab so the attendee keeps their place in the form.
- **Em dashes and placeholder text.** None. Scanned the served HTML of all three pages
  plus the stylesheet and `copy.js` for em dash, en dash, ellipsis, lorem, ipsum, TODO
  and FIXME. All clean.
- **Reaching step 2 by URL.** Not possible. There is no session, the flow carries state
  in hidden form fields, and a GET is refused. See M1 for what the refusal looks like.
- **Recovering a signup.** Re-walking the form with the same address returns the same
  page rather than an error, so an attendee who loses the tab can get back to their
  state by starting over. `390-resubmit-same-email.png`.

## Verdict

Not yet fit to put in front of a room, and the gap is copy rather than code. The
machinery is sound: the three steps work on a real phone at 360 and 390 and on a
1440 desktop, nothing clips or overflows, nothing scrolls sideways, the buttons are
56 pixels tall and obviously pressable, the contrast measures between 4.6:1 and 16:1,
the step indicator is honest, the Discord button goes to the real RunPod server in a
new tab, and every input I abused was either refused cleanly or absorbed without a
stack trace. What fails is the one page every attendee will actually see today. It
tells them the credits are all taken, which is false and reads as a race they lost,
and it promises an email that no part of this application can send. Rewrite those two
sentences in `no_link.html` and echo back the address you captured, and the event path
is ready. Add the `GET` handlers so a revisit stops showing raw JSON, and the whole
thing is solid.
