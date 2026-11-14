# SmartStock AI

This is the helper for your kota shop.

Owners send a WhatsApp message or an Instagram message.
The helper writes down what they sold, bought, and threw away.
It can also say what to prep, what to buy, and if the shop made money.

It does not guess a price or a stock number.
If it is not sure, it asks.
Nothing is saved until the owner replies YES.

Try messages like these:

1. sold 12 chicken kota 8 russian
2. bought 20 bread R180
3. threw away 5 chips burnt

# Show it to judges

This works even if WhatsApp or Instagram is not linked yet.

1. On Render, set `SEED_DEMO` to `true`.
2. Redeploy.
3. Open `https://YOUR-ADDRESS.onrender.com/demo`.
4. Pick a business. Tap a question.

The page uses the same helper as the real chats.
It does not invent prices. The kota sheet has no menu prices, so those answers say the prices are missing.

# Put it on the internet

Do these steps in order.
Do not skip a step.

Meta cannot call your laptop.
The helper must be on the internet first.

## 1. Put the project on GitHub

Render needs a git copy of this project.
If you do not have one yet, click **Create repo** in Cursor.
Then come back here.

## 2. Open Render

1. Go to [render.com](https://render.com).
2. Sign up.
3. Click **New**.
4. Click **Blueprint**.
5. Connect the git repo from step 1.

Render reads `render.yaml`.
It makes the helper and the database.

## 3. Fill in the empty boxes

Render shows empty boxes.
Copy each value from Meta.
Type them into the box with the same name.

Leave **LLM_API_KEY** empty if you do not have one yet.
The example messages above still work.

| Box on Render | Where to copy it |
| --- | --- |
| META_APP_SECRET | Meta → your app → **App settings** → **Basic** → **App secret** → Show |
| WEBHOOK_VERIFY_TOKEN | Make up a long password. You will paste the same password into Meta in step 6. |
| WHATSAPP_TOKEN | Meta → **WhatsApp** → **API setup** → token |
| WHATSAPP_PHONE_NUMBER_ID | Meta → **WhatsApp** → **API setup** → **Phone number ID** |
| INSTAGRAM_TOKEN | Meta → **Instagram** → token for the shop's Instagram account |
| INSTAGRAM_ACCOUNT_ID | Meta → **Instagram** → the Instagram account id |
| LLM_API_KEY | Optional. An OpenAI key, or another OpenAI-style key. Blank is ok. |
| LLM_BASE_URL | Leave `https://api.openai.com/v1` unless your key is from another company. |
| LLM_MODEL | Leave `gpt-4o-mini` unless you were told another model name. |
| DATABASE_URL | Leave this. Render fills it in. |

Do not put these passwords in a chat or in the code.
They belong only in Render's boxes.

## 4. Deploy

Click **Apply** or **Deploy**.
Wait until the page says **Live**.

Copy the web address.
It looks like `https://smartstock-ai.onrender.com`.

## 5. Check that it is alive

Open this address in a browser:

`https://YOUR-ADDRESS.onrender.com/health`

You should see:

`{"status":"ok"}`

If you do not, wait one minute and try again.
Do not go to Meta until this works.

## 6. WhatsApp webhook

In Meta, open your app.
Go to **WhatsApp** → **Configuration**.

Callback URL. Paste this. Use your real Render address:

`https://YOUR-ADDRESS.onrender.com/webhook`

Verify token. Paste the same password you put in **WEBHOOK_VERIFY_TOKEN**.

Click **Verify and save**.

Then find **Webhook fields**.
Subscribe to **messages**.
Only that field is required.

While the Meta app is in **Development**, add the owner's phone under **WhatsApp** → **API setup**.
Meta blocks other phones until the app is approved.

## 7. Instagram webhook

Use the same callback URL.
Use the same verify token.

Where to paste them:

1. Meta → **Instagram** → **API setup with Instagram login** → webhooks.
2. Or Meta → **App settings** → **Webhooks** → Instagram.

Click **Verify and save**.
Subscribe to **messages**.

The shop needs a professional Instagram account linked to this Meta app.
The owner must message that account.

## 8. Make the automatic messages

WhatsApp only allows a normal reply for 24 hours after the owner writes.
The helper sends three messages on its own.
Those must be templates.

In WhatsApp Manager, create a template.
Category: **Utility**.
Language: **English**.
Body: type only this:

`{{1}}`

Create these four names. Copy them exactly.

1. `smartstock_morning_briefing` — every day at 07:00
2. `smartstock_evening_checkin` — every day at 19:00
3. `smartstock_weekly_wrap` — Sunday at 18:00
4. `smartstock_reply` — only if a reply is more than 24 hours late

Times are South Africa time.

For Meta's sample text, paste:

`Morning. I need yesterday's sales before I can say what to prep.`

Wait until each template says **Approved**.
Chat still works before that, if the owner has written in the last 24 hours.

Use one Render instance.
Two copies would try to send the morning message twice.
The database stops a second send of the same message.

## 9. Try it from your phone

1. Send `sold 12 chicken kota` on WhatsApp.
2. The first time, the helper asks you to reply YES. That is your permission to store the shop's numbers.
3. Send the sale again.
4. The helper reads it back.
5. Reply YES.
6. It is saved.

Ask `profit this month` or `what should I prep` when you want a report.
If a number is missing, the helper says it is missing.
It will not fill it in.

To delete the shop's records, send `DELETE MY DATA`.
Then reply YES.

# If Verify fails

1. `/health` must work first.
2. The address must start with `https://`.
3. The address must end with `/webhook`.
4. The verify password in Meta must match **WEBHOOK_VERIFY_TOKEN**.
5. No extra space at the start or end.

# Run it on a computer

This is for the person who sets the computer up.

1. Install Docker.
2. In this folder, run `docker compose up --build`.
3. Open [http://127.0.0.1:43123/health](http://127.0.0.1:43123/health).
4. Install the test tools: `pip install -r requirements-dev.txt`.
5. Run `python scripts/simulate_webhook.py`.

That script sends a pretend WhatsApp message and a pretend Instagram message.
It checks that both are saved and that a reply was sent.
The keys in `docker-compose.yml` are fake.
They are only for this test.

To run the tests: `python -m pytest`.

# What the helper keeps

Sales, purchases, expenses, waste, invoices, stock, and the chat.
Money is rand.
The clock is South Africa time.
Each phone number and each Instagram id is its own shop.
