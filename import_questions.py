#!/usr/bin/env python3
"""
Bulk import questions from a JSON file.

Usage:
    python import_questions.py questions.json

JSON format:
[
  {
    "question": "What is the capital of France?",
    "options": ["London", "Paris", "Berlin", "Madrid"],
    "answer_idx": 1,
    "hint": "It's on the Seine river",
    "explanation": "Paris has been the capital of France since 987 AD.",
    "category": "Geography"
  }
]
"""

import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from database.db import Database
from config.settings import settings


async def main():
    if len(sys.argv) < 2:
        print("Usage: python import_questions.py questions.json")
        sys.exit(1)

    filepath = sys.argv[1]
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    db = Database(settings.DATABASE_URL)
    await db.initialize()

    success = 0
    errors = 0
    for i, item in enumerate(data):
        try:
            qid = await db.add_question(
                created_by=0,  # system import
                question=item["question"],
                options=item["options"],
                answer_idx=item["answer_idx"],
                hint=item.get("hint"),
                explanation=item.get("explanation"),
                category=item.get("category", "general")
            )
            print(f"  ✅ [{i+1}/{len(data)}] Added question #{qid}: {item['question'][:50]}...")
            success += 1
        except Exception as e:
            print(f"  ❌ [{i+1}/{len(data)}] Error: {e}")
            errors += 1

    await db.close()
    print(f"\n📊 Import complete: {success} added, {errors} failed.")


if __name__ == "__main__":
    asyncio.run(main())
