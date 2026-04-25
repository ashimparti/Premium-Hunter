name: Daily Premium Hunt

on:
  schedule:
    # Mon-Thu 6 AM Dubai (morning prep)
    - cron: '0 2 * * 1-4'
    # Mon-Thu 5 PM Dubai (pre-fire IV refresh, before next-day US open)
    - cron: '0 13 * * 1-4'
    # Friday 5 PM Dubai = weekend prep, catches Mon BMO/AMC + Tue earnings
    - cron: '0 13 * * 5'
  # Allow manual trigger from GitHub UI
  workflow_dispatch:

jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt

      - name: Run premium hunter
        run: python premium_hunter.py

      - name: Email the report
        uses: dawidd6/action-send-mail@v3
        with:
          server_address: smtp.gmail.com
          server_port: 465
          secure: true
          username: ${{ secrets.EMAIL_USERNAME }}
          password: ${{ secrets.EMAIL_PASSWORD }}
          subject: "🎯 Premium Hunt Report"
          to: ${{ secrets.EMAIL_USERNAME }}
          from: Premium Hunter Bot
          html_body: file://report.html
          attachments: report.html,scan_results.json

      - name: Save report as artifact
        uses: actions/upload-artifact@v4
        with:
          name: premium-hunt-report
          path: |
            report.html
            scan_results.json
          retention-days: 30
