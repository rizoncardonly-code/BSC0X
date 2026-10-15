# binance_pay.py
import logging
from datetime import datetime, timedelta
from binance_common.configuration import ConfigurationRestAPI
from binance_common.constants import PAY_REST_API_PROD_URL
from binance_sdk_pay.pay import Pay

logging.basicConfig(level=logging.INFO)


def get_binance_pay_client(api_key: str, api_secret: str):
    configuration = ConfigurationRestAPI(
        api_key=api_key,
        api_secret=api_secret,
        base_path=PAY_REST_API_PROD_URL
    )
    return Pay(config_rest_api=configuration)


def find_transaction_by_order_id(client: Pay, order_id: str, lookback_days: int = 7):
    """
    শেষ lookback_days দিনের মধ্যে order_id (transactionId) খুঁজে বের করে।
    Binance Pay API শুধুমাত্র শেষ ৯০ দিনের ডেটা রিটার্ন করে।
    """
    end_time = int(datetime.now().timestamp() * 1000)
    start_time = int((datetime.now() - timedelta(days=lookback_days)).timestamp() * 1000)

    try:
        response = client.rest_api.get_pay_trade_history(
            startTimestamp=start_time,
            endTimestamp=end_time,
            limit=100
        )
        data = response.data()

        for tx in data:
            if tx.get("transactionId") == order_id:
                return {
                    "found": True,
                    "transactionId": tx.get("transactionId"),
                    "amount": tx.get("amount"),
                    "currency": tx.get("currency"),
                    "transactionTime": tx.get("transactionTime"),
                }
        return {"found": False}

    except Exception as e:
        logging.error(f"Binance Pay API error: {e}")
        return {"found": False, "error": str(e)}
