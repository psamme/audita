"""Keep uploaded training decisions separate from new, undecided receipts."""
import json
import re
import sqlite3
from shadow import db, playbook
from shadow.onboard import company

INCOMING_ROLES = {'bank_lines', 'ledger_entries', 'invoices', 'documents'}
DECISION_COLUMNS = {'decision', 'resolution', 'action', 'expected_action', 'correct_action', 'ground_truth',
                    'label', 'reconciled_by', 'reconciled_at', 'approved_by', 'approver', 'booked_account', 'writeoff_account'}


def cutoff(client):
    pb = playbook.load(client, 'main')
    if pb:
        return pb['trained_before']
    if company.CONFIG.exists():
        cfg = json.loads(company.CONFIG.read_text())
        if cfg.get('client') == client:
            return cfg.get('training_before')


def freeze(client, before):
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', before or ''):
        raise ValueError('Choose a valid training cutoff month.')
    existing = cutoff(client)
    if existing and before != existing:
        raise ValueError('Training is fixed to the original history window. New receipts cannot become training answers.')
    path = db.DATA / client / 'training_snapshot.db'
    if not path.exists():
        src = db.connect(client, readonly=True)
        dest = sqlite3.connect(path)
        try:
            src.backup(dest)
        finally:
            dest.close()
            src.close()
    cfg = json.loads(company.CONFIG.read_text())
    cfg['training_before'] = before
    company.CONFIG.write_text(json.dumps(cfg, indent=1))


def check_upload(client, purpose, role, header):
    if purpose not in {'history', 'incoming'}:
        raise ValueError('Choose history or new receipts for this upload.')
    if purpose == 'incoming':
        if not cutoff(client):
            raise ValueError('Learn your policies from historical decisions before uploading new receipts.')
        if role not in INCOMING_ROLES:
            raise ValueError('New receipts cannot include past reconciliation decisions, adjustments or approvals.')
        names = {re.sub(r'[^a-z0-9]+', '_', h.lower()).strip('_') for h in header}
        if names & DECISION_COLUMNS:
            raise ValueError('Remove decision columns from new receipts: ' + ', '.join(sorted(names & DECISION_COLUMNS)))
    elif cutoff(client):
        raise ValueError('Your training window is fixed. Use New receipts for subsequent uploads.')


def check_rows(client, purpose, role, rows):
    before = cutoff(client)
    if purpose == 'incoming':
        if not before or role not in INCOMING_ROLES:
            raise ValueError('Learn your policies first and upload only undecided source records.')
        for _, row in rows:
            if role in {'bank_lines', 'ledger_entries'} and (row.get('date') or '')[:7] < before:
                raise ValueError(f'New records must be dated {before} or later. Your earlier history is reserved for learning.')
    elif before:
        raise ValueError('Training is already fixed. Use New receipts for later records.')


def run_period(client, period):
    before = cutoff(client)
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', period or ''):
        raise ValueError('Choose a valid receipt month.')
    if not before or period < before:
        raise ValueError('Choose a new-receipt month after your training history.')
