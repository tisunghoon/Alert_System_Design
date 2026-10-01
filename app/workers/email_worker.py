from app.workers.base_worker import BaseWorker, run_worker


class EmailWorker(BaseWorker):
    channel = "email"


if __name__ == "__main__":
    run_worker(EmailWorker)
