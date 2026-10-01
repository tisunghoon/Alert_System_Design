from app.workers.base_worker import BaseWorker, run_worker


class SMSWorker(BaseWorker):
    channel = "sms"


if __name__ == "__main__":
    run_worker(SMSWorker)
