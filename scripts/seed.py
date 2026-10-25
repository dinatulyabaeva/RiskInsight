import argparse
from pathlib import Path
from app.config import Settings
from app.db import seed_database

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Create deterministic synthetic portfolio data')
    parser.add_argument('--loans', type=int, default=10000)
    parser.add_argument('--database', type=Path, default=Settings.from_env().db_path)
    parser.add_argument('--force', action='store_true', help='Replace existing demo DB; never use for real data')
    args = parser.parse_args()
    seed_database(args.database, args.loans, force=args.force)
    print(f'Synthetic dataset ready: {args.database}')
