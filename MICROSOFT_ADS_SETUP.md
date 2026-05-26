# Microsoft Ads API Setup — 30 minutes

Once done, send me 3 things and I'll wire it into the ROI Tracker:
- Customer Number (10 digits)
- Developer Token
- n8n credential ID (you'll create the credential at the end)

---

## Step 1 — Find your Customer Number (1 min)
1. https://ads.microsoft.com (sign in)
2. Top right → **Help** → **About** → look for **Account number** (10 digits, no dashes)

## Step 2 — Get a Developer Token (3 min)
1. https://developers.ads.microsoft.com/Account → log in with same Microsoft account
2. Click **Request token** → fills out automatically
3. Copy the developer token shown. **Basic tier is fine** for daily reads (same as Google Ads).

## Step 3 — Register an OAuth application (10 min)
1. Go to https://portal.azure.com (sign in with your Microsoft account)
2. Top search bar → **App registrations** → **+ New registration**
3. Name: `liftnow-n8n`
4. Supported account types: **Personal Microsoft accounts only** (or "Any Microsoft Entra directory" if Liftnow is on M365 — your call, doesn't matter for read-only API)
5. Redirect URI: pick **Web** → paste:
   ```
   https://agents.liftnowdirect.com/rest/oauth2-credential/callback
   ```
6. Click **Register**
7. From the new app's overview page, copy:
   - **Application (client) ID**
   - **Directory (tenant) ID** (may not need this depending on account type)
8. Left menu → **Certificates & secrets** → **+ New client secret** → name `n8n-prod`, expires 24 months → **Add**
9. **Copy the Value column IMMEDIATELY** (it never shows again). Save it.

## Step 4 — Grant Ads API permission (3 min)
1. In the same app → **API permissions** → **+ Add a permission**
2. Click **APIs my organization uses** → search "ads"
3. Pick **Bing Ads** (or "Microsoft Ads") → **Delegated permissions** → check **ads.manage**
4. **Add permissions**
5. (Optional, but recommended) Click **Grant admin consent** so you don't re-consent on each token refresh

## Step 5 — Create n8n credential (5 min)
1. https://agents.liftnowdirect.com → top-right avatar → **Credentials** → **+ Add credential**
2. Search → **Microsoft OAuth2 API** (or "Microsoft Ads OAuth2 API" if it exists in your n8n version)
3. Fill in:
   - **Client ID**: from Step 3
   - **Client Secret**: from Step 3 (the Value)
   - **Authorization URL**: `https://login.microsoftonline.com/common/oauth2/v2.0/authorize`
   - **Access Token URL**: `https://login.microsoftonline.com/common/oauth2/v2.0/token`
   - **Scope**: `https://ads.microsoft.com/msads.manage offline_access`
4. Click **Sign in with Microsoft** → consent
5. Name it `Microsoft Ads OAuth2 (liftnow)` → Save
6. From the URL bar when viewing this saved credential, copy the credential ID (16 chars)

## Step 6 — Send me

In a chat reply, paste:
```
Customer Number: 1234567890
Developer Token: AAA111BBB222CCC333
Credential ID: XyZ123abc456DEF7
```

I'll add a "Pull Microsoft Ads Daily" HTTP node into the ROI Tracker workflow that joins the two sources into one daily spend/conversions chart.

---

## Honest caveats

- **OAuth refresh tokens can expire** if you don't grant `offline_access` scope. Make sure that's in your scope list.
- **Bing Ads / Microsoft Ads API** is much sloppier than Google's. Reports use SOAP+XML by default but they have a REST endpoint — I'll use REST.
- **Conversion tracking** in Microsoft Ads has to be set up separately (UET tag on liftnow.com). If you haven't done this, conversions will be 0 even when you have campaigns running.
- **Developer Token approval**: Microsoft is faster than Google (usually instant or a few hours for Basic tier).
