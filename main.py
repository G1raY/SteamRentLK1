from botHandler.bot import main
from funpayHandler.funpay import startFunpay

import threading
import asyncio


if __name__ == "__main__":
    funpay_thread = threading.Thread(target=startFunpay)
    funpay_thread.daemon = True
    funpay_thread.start()

    main()
