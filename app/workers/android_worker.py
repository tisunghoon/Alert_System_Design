from app.workers.base_worker import BaseWorker, run_worker


class AndroidWorker(BaseWorker):
    channel = "android"


if __name__ == "__main__":
    run_worker(AndroidWorker)
