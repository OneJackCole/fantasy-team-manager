from yahoo_oauth import OAuth2
import yahoo_fantasy_api as yfa

def test_connection():
    # Automatically reads your keys from oauth2.json
    sc = OAuth2(None, None, from_file='oauth2.json')
    
    if not sc.token_is_valid():
        sc.refresh_access_token()
        
    gm = yfa.Game(sc, 'nhl')
    league_ids = gm.league_ids()
    
    print("Successfully connected!")
    print(f"Your NHL League IDs: {league_ids}")

if __name__ == '__main__':
    test_connection()