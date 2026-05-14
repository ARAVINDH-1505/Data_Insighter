import argparse
import os

from dotenv import load_dotenv

from refresh_job_service import run_refresh_worker


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description='Run the Data Insighter refresh worker.')
    parser.add_argument('--managed-dir', default=os.path.join(os.path.dirname(__file__), 'uploads', 'managed'))
    parser.add_argument('--poll-seconds', type=int, default=30)
    parser.add_argument('--iterations', type=int, default=None)
    args = parser.parse_args()

    runner_id = run_refresh_worker(
        managed_dir=args.managed_dir,
        poll_seconds=args.poll_seconds,
        iterations=args.iterations,
    )
    print(f'Refresh worker completed with runner id {runner_id}')


if __name__ == '__main__':
    main()
