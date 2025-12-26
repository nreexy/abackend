import json
from app.services.prh import format_prh_result

# User provided example response
example_response = {
  "status": "ok",
  "recordCount": 1,
  "data": {
    "titles": [
      {
        "isbn": 9780525640370,
        "title": "Where the Crawdads Sing: Reese's Book Club",
        "subtitle": None,
        "author": "Delia Owens",
        "onsale": "2018-08-14",
        "pages": None,
        "imprint": {
          "code": "IA",
          "description": "Penguin Audio"
        },
        "publisher": {
          "code": "2222",
          "description": "Penguin Random House Audio Publishing Group"
        },
        "series": None,
        "seriesnumber": None
      }
    ]
  }
}

def test_parsing():
    item = example_response["data"]["titles"][0]
    formatted = format_prh_result(item)
    
    print("Formatted Result:")
    print(json.dumps(formatted, indent=2))
    
    # Assertions based on user data
    if formatted["title"] != "Where the Crawdads Sing: Reese's Book Club":
        print("❌ Title mismatch")
    else:
        print("✅ Title matched")
        
    if "Delia Owens" not in formatted["authors"]:
        print(f"❌ Author mismatch: {formatted['authors']}")
    else:
        print("✅ Author matched")

if __name__ == "__main__":
    test_parsing()
