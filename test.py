import requests
import json

def get_serpapi_text(query, api_key, top_n=5):
    """
    Retrieves JSON from SerpAPI for a given query and extracts maximum relevant text data, limited to top N organic results.
    
    Args:
        query (str): The search query (e.g., "latest ai news").
        api_key (str): Your SerpAPI key.
        top_n (int): Number of organic results to return (default 5).
    
    Returns:
        dict: Filtered JSON with query_displayed, top N organic_results, and related_searches.
    """
    # SerpAPI endpoint and parameters
    url = "https://serpapi.com/search.json"
    params = {
        "engine": "google",
        "q": query,
        "hl": "en",
        "gl": "us",
        "google_domain": "google.com",
        "num": "10",
        "start": "0",  # Start at 0 for freshest results
        "safe": "active",
        "api_key": api_key
    }
    
    try:
        # Make GET request
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        
        # Initialize filtered output
        filtered_data = {
            "query_displayed": "",
            "organic_results": [],
            "related_searches": []
        }
        
        # Extract query_displayed
        if "search_information" in data and "query_displayed" in data["search_information"]:
            filtered_data["query_displayed"] = data["search_information"]["query_displayed"]
        
        # Filter organic_results (top N)
        if "organic_results" in data:
            for result in data["organic_results"][:top_n]:
                filtered_result = {
                    "title": result.get("title", ""),
                    "snippet": result.get("snippet", ""),
                    "source": result.get("source", "")
                }
                filtered_data["organic_results"].append(filtered_result)
        
        # Filter related_searches
        if "related_searches" in data:
            for search in data["related_searches"]:
                if "query" in search:
                    filtered_data["related_searches"].append(search["query"])
                elif "items" in search:
                    for item in search["items"]:
                        if "name" in item:
                            filtered_data["related_searches"].append(item["name"])
        
        return filtered_data
    
    except requests.exceptions.RequestException as e:
        print(f"Error making API request: {e}")
        return {}
    except json.JSONDecodeError:
        print("Error decoding JSON response")
        return {}
    except Exception as e:
        print(f"Unexpected error: {e}")
        return {}

# Example usage
if __name__ == "__main__":
    query = "lONDON WEATHER"
    api_key = "977d1ae964cb7b4204cb0cd63cdf2f4bf4b52d26bd5bfe876bd039ebf98b7c1"  # Replace with your SerpAPI key
    result = get_serpapi_text(query, api_key)
    print(json.dumps(result, indent=2))