# Discord failure acknowledgement and escalation

This feature uses the existing Discord webhook for messages and a Discord bot only to read reactions on the exact alert message. On a failure, a named responder can react with ✅ within 15 minutes. If none of the responders for that alert reacts, the webhook sends one follow-up that mentions the agent owner. If reaction lookup itself fails, the webhook immediately tells the agent owner that acknowledgement checking failed.

Success alerts do not wait. If the bot token, channel ID, or required owner IDs are missing, the original failure alert is still sent without the 15-minute wait; the Actions log shows a warning.

## What the repository owner must configure

1. In the Discord Developer Portal, create an application and bot, then add the bot to the project server and the CI/CD alert channel.
2. Give the bot only **View Channel** and **Read Message History** in that channel. It does not need Administrator permission. The existing webhook sends the messages.
3. In the repository's **Settings → Secrets and variables → Actions**, add:
   - Secret `DISCORD_BOT_TOKEN`: the bot token. Never commit or paste this value into chat.
   - Variable or secret `DISCORD_CHANNEL_ID`: ID of the alert channel the bot can read.
   - Confirm the existing owner IDs are configured: `DISCORD_BACKEND_ID`, `DISCORD_FRONTEND_ID`, `DISCORD_DEPLOY_ID`, and `DISCORD_AGENT_ID`. The workflows accept these as either repository secrets or variables.
4. Turn on Developer Mode in Discord to copy the channel and member IDs. The member ID for `DISCORD_AGENT_ID` must be 박서윤.

## Acknowledgement rules

- CI failures wait for ✅ from the responder(s) mentioned in that CI alert.
- Deployment failures wait for ✅ from `DISCORD_DEPLOY_ID`.
- The timer starts after the first alert is posted. Any eligible responder's ✅ stops escalation.
- After 15 minutes without an eligible reaction, 박서윤 is mentioned once.
- If the bot cannot read the reactions, 박서윤 is notified immediately that the check failed.

## Run the real test

After the bot and repository settings are ready, open **Actions → Discord 15-minute acknowledgement test → Run workflow** and select `dev`.

- To verify acknowledgement: react to the `[TEST]` alert with ✅ before 15 minutes. The run should finish as `acknowledged` without a second alert.
- To verify escalation: leave the `[TEST]` alert without ✅. After 15 minutes, the run should finish as `escalated` and mention 박서윤.
- This manual run lasts up to 15 minutes and sends a real, clearly marked test message to the Discord channel.
