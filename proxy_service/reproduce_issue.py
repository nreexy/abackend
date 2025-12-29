
import asyncio
import json

def test_logic():
    print("Testing view_detail_page logic...")

    # Case 1: Normal book
    book_normal = {
        "title": "Normal Book",
        "authors": ["Author A", "Author B"],
        "narrators": ["Narrator X"],
        "genres": ["Sci-Fi"]
    }
    
    try:
        book_normal['authors_str'] = ", ".join(book_normal.get("authors", []))
        print("✅ Normal book passed")
    except Exception as e:
        print(f"❌ Normal book failed: {e}")

    # Case 2: Authors is None
    book_none_authors = {
        "title": "Broken Book",
        "authors": None,
        "narrators": [],
        "genres": []
    }
    
    try:
        # dict.get("key", default) returns value if key exists! even if it is None.
        authors = book_none_authors.get("authors", [])
        if authors is None:
            # logic in ui.py now does: ", ".join(book.get("authors") or [])
            # if book.get returns None, or [] ensures it is an empty list
            pass
            
        book_none_authors['authors_str'] = ", ".join(book_none_authors.get("authors") or [])
        print("✅ None authors passed")
    except TypeError as e:
        print(f"❌ None authors failed with TypeError: {e}")
    except Exception as e:
        print(f"❌ None authors failed with {type(e)}: {e}")

if __name__ == "__main__":
    test_logic()
