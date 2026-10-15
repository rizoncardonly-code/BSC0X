# database.py
import firebase_admin
from firebase_admin import credentials, db
from datetime import datetime
import os
from dotenv import load_dotenv

load_dotenv()

cred = credentials.Certificate("serviceAccount.json")
firebase_admin.initialize_app(cred, {
    'databaseURL': os.getenv("FIREBASE_DATABASE_URL")
})


def create_order(user_id: int, order_id: str, amount: float, forward_to: str = None):
    """নতুন অর্ডার তৈরি করে। order_id unique key হিসেবে ব্যবহৃত হয়।"""
    ref = db.reference(f'orders/{order_id}')
    if ref.get():
        return False
    ref.set({
        'telegram_user_id': user_id,
        'order_id': order_id,
        'amount': amount,
        'currency': 'USDT',
        'fee': 0,
        'net_amount': 0,
        'status': 'pending',
        'forward_to': forward_to or '',
        'created_at': datetime.now().isoformat(),
        'verified_at': None
    })
    return True


def get_order(order_id: str):
    return db.reference(f'orders/{order_id}').get()


def mark_verified(order_id: str, amount: float = None):
    ref = db.reference(f'orders/{order_id}')
    data = {
        'status': 'verified',
        'verified_at': datetime.now().isoformat()
    }
    if amount is not None:
        data['amount'] = amount
    ref.update(data)


def record_fee(order_id: str, fee_amount: float, currency: str = 'USDT'):
    db.reference(f'fees/{order_id}').set({
        'order_id': order_id,
        'fee_amount': fee_amount,
        'currency': currency,
        'collected_at': datetime.now().isoformat()
    })


def update_order_with_fee(order_id: str, fee: float, net_amount: float):
    db.reference(f'orders/{order_id}').update({
        'fee': fee,
        'net_amount': net_amount,
        'status': 'forwarded'
    })
