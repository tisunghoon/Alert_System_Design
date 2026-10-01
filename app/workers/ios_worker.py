from app.workers.base_worker import BaseWorker, run_worker


class IOSWorker(BaseWorker):
    channel = "ios"


if __name__ == "__main__":
    run_worker(IOSWorker)
