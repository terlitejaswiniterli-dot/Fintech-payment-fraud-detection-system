# Partition-Tolerant Payment Authorization with Inline Fraud Screening

## 🛡️ Overview

A fintech payment security system that **checks every transaction for fraud before authorization** and continues to handle payments safely even when the network is unavailable.

## 🚀 Key Features

* 🔍 **Inline Fraud Screening** – Detects suspicious transactions before payment authorization.
* 📊 **Risk Scoring** – Calculates a risk score using transaction details such as amount, device, location, and transaction behavior.
* 🔐 **Idempotency** – Prevents duplicate payments when the same request is submitted multiple times.
* 👥 **Dynamic Recipients** – Allows users to make payments to different recipients.
* 📒 **Transaction Ledger** – Maintains transaction states and payment history.
* 🔄 **Settlement & Reconciliation** – Handles settlement failures and retries transactions.
* 📡 **Partition Tolerance** – Provides controlled payment handling during temporary network failures.

## 🔄 How It Works

```text
Payment Request
       ↓
Idempotency Check
       ↓
Fraud Screening
       ↓
Risk Score
       ↓
Authorization Decision
   ↓      ↓       ↓
Approve  Verify  Decline
       ↓
Settlement
       ↓
Reconciliation
```

## 🛠️ Technology

* **Frontend:** HTML, CSS, JavaScript
* **Backend:** Python, FastAPI
* **Database:** SQLite
* **Fraud Detection:** Rule-based + hybrid risk scoring

## 🎯 Purpose

The project demonstrates how a payment system can provide **fraud protection, duplicate-payment prevention, and reliable transaction processing** even during network or settlement failures.

> **Note:** This is a hackathon prototype. No real financial transactions are processed.
