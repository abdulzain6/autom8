import requests
from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString
from typing import List, Dict, Any, Union, Optional
from aci.common.db.sql_models import LinkedAccount
from aci.common.schemas.security_scheme import NoAuthScheme, NoAuthSchemeCredentials
from aci.server.app_connectors.base import AppConnectorBase
from aci.server.config import HTTP_PROXY
from aci.common.logging_setup import get_logger
import re

logger = get_logger(__name__)


class CricbuzzConnector(AppConnectorBase):
    """
    A custom, AI-friendly connector that scrapes Cricbuzz for live matches and full scorecards,
    with full proxy support and robust, targeted parsing.
    """

    def __init__(
        self,
        linked_account: LinkedAccount,
        security_scheme: NoAuthScheme,
        security_credentials: NoAuthSchemeCredentials,
        run_id: str | None = None,
    ):
        """
        Initializes the connector and sets up a persistent, proxy-aware requests session.
        """
        super().__init__(linked_account, security_scheme, security_credentials, run_id=run_id)

        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        })

        if HTTP_PROXY:
            logger.info("Configuring CricbuzzConnector with proxy.")
            self.session.proxies.update({'http': HTTP_PROXY, 'https': HTTP_PROXY})
        else:
            logger.info("Initializing CricbuzzConnector without proxy.")

        try:
            self.session.get("https://www.cricbuzz.com", timeout=15).raise_for_status()
            logger.info("CricbuzzConnector initialized and connection verified.")
        except Exception as e:
            logger.error(f"Failed to initialize CricbuzzConnector: {e}")
            raise ConnectionError(f"Could not connect to Cricbuzz. Check proxy/network. Error: {e}")
        
    def _before_execute(self) -> None:
        return super()._before_execute()

    def get_live_matches(self) -> List[Dict[str, Any]]:
        """
        Fetches and parses live match data directly from the Cricbuzz live scores page.
        """
        url = "https://www.cricbuzz.com/cricket-match/live-scores"
        logger.info(f"Scraping live matches from {url}...")
        try:
            response = self.session.get(url)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            matches: List[Dict[str, Any]] = []
            # Updated selector to find all match list items
            match_items = soup.select("div.cb-mtch-lst.cb-col.cb-col-100.cb-tms-itm")

            for item in match_items:
                if not isinstance(item, Tag):
                    continue
                match_data: Dict[str, Any] = {}
                
                # Get series name from the preceding h2 element
                series_element = item.find_previous("h2", class_="cb-lv-grn-strip")
                if series_element and isinstance(series_element, Tag):
                    series_anchor = series_element.find("a")
                    if series_anchor and isinstance(series_anchor, Tag):
                        match_data["series"] = series_anchor.get_text(strip=True)
                else:
                    match_data["series"] = "Uncategorized"

                # Get match title/teams
                title_element = item.find("h3", class_="cb-lv-scr-mtch-hdr")
                if not (title_element and isinstance(title_element, Tag)):
                    continue
                
                title_anchor = title_element.find("a")
                if title_anchor and isinstance(title_anchor, Tag):
                    match_data["title"] = title_anchor.get_text(strip=True)
                    href = title_anchor.get("href")
                    if href and isinstance(href, str):
                        match_data["url"] = "https://www.cricbuzz.com" + href
                else:
                    continue

                # Get match description
                description_element = title_element.find_next_sibling("span", class_="text-gray")
                if description_element and isinstance(description_element, Tag):
                    match_data["description"] = description_element.get_text(strip=True)
                else:
                    match_data["description"] = ""

                # Get match status
                status_element = item.find("div", class_=["cb-text-live", "cb-text-complete"])
                if status_element and isinstance(status_element, Tag):
                    match_data["status"] = status_element.get_text(strip=True)
                else:
                    match_data["status"] = "Status not available"

                # Get scorecard URL
                scorecard_anchor = item.find("a", title="Scorecard")
                if scorecard_anchor and isinstance(scorecard_anchor, Tag):
                    href = scorecard_anchor.get("href")
                    if href and isinstance(href, str):
                        match_data["scorecard_url"] = "https://www.cricbuzz.com" + href
                else:
                    match_data["scorecard_url"] = None
                
                matches.append(match_data)
            
            return matches
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch the page: {e}")
            return [{"error": "Network request failed."}]
        except Exception as e:
            logger.error(f"An error occurred during scraping: {e}")
            return [{"error": f"Failed to parse the page content: {e}"}]

    def get_full_scorecard(self, scorecard_url: str) -> Dict[str, Any]:
        """
        Scrapes a full, detailed scorecard from a given Cricbuzz match URL.
        """
        logger.info(f"Scraping full scorecard from {scorecard_url}...")
        try:
            response = self.session.get(scorecard_url)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")

            scorecard: Dict[str, Any] = {"match_info": {}, "innings": []}

            # --- Scrape Match Info ---
            match_info_div = soup.find("div", class_="cb-col-100", text="Match Info")
            if match_info_div and isinstance(match_info_div, Tag):
                info_wrapper = match_info_div.find_parent("div", class_="cb-col-100")
                if info_wrapper and isinstance(info_wrapper, Tag):
                    for item in info_wrapper.find_all("div", class_="cb-mtch-info-itm"):
                        if isinstance(item, Tag):
                            key_elem = item.find("div", class_="cb-col-27")
                            val_elem = item.find("div", class_="cb-col-73")
                            if key_elem and isinstance(key_elem, Tag) and val_elem and isinstance(val_elem, Tag):
                                key = key_elem.get_text(strip=True)
                                value = val_elem.get_text(strip=True)
                                scorecard["match_info"][key] = value

            # --- Scrape Each Innings ---
            innings_divs = soup.find_all("div", id=re.compile(r"^innings_"))
            for innings_div in innings_divs:
                if not isinstance(innings_div, Tag):
                    continue
                inning_data: Dict[str, Any] = {"batting": [], "bowling": [], "fall_of_wickets": []}

                header = innings_div.find("div", class_="cb-scrd-hdr-rw")
                if header and isinstance(header, Tag):
                    inning_data["title"] = header.get_text(strip=True, separator=" - ")
                else:
                    inning_data["title"] = "Innings"

                # --- Batting Scorecard ---
                player_rows = innings_div.select("div.cb-scrd-itms")
                for row in player_rows:
                    if not isinstance(row, Tag):
                        continue
                    cols = [col for col in row.find_all("div", class_="cb-col") if isinstance(col, Tag)]
                    if len(cols) >= 7 and cols[0].find("a"):
                        player_link = cols[0].find("a")
                        if player_link and isinstance(player_link, Tag):
                            player_name = player_link.get_text(strip=True)
                        else:
                            player_name = cols[0].get_text(strip=True)
                        inning_data["batting"].append({
                            "player": player_name,
                            "dismissal": cols[1].get_text(strip=True) if len(cols) > 1 else "",
                            "R": cols[2].get_text(strip=True) if len(cols) > 2 else "",
                            "B": cols[3].get_text(strip=True) if len(cols) > 3 else "",
                            "4s": cols[4].get_text(strip=True) if len(cols) > 4 else "",
                            "6s": cols[5].get_text(strip=True) if len(cols) > 5 else "",
                            "SR": cols[6].get_text(strip=True) if len(cols) > 6 else "",
                        })

                # --- Fall of Wickets ---
                fow_header = innings_div.find("div", class_="cb-scrd-sub-hdr", text="Fall of Wickets")
                if fow_header and isinstance(fow_header, Tag):
                    next_div = fow_header.find_next_sibling("div")
                    if next_div and isinstance(next_div, Tag):
                        fow_spans = [span for span in next_div.find_all("span") if isinstance(span, Tag)]
                        inning_data["fall_of_wickets"] = [fow.get_text(strip=True) for fow in fow_spans]

                # --- Bowling Scorecard ---
                bowling_header = innings_div.find("div", class_="cb-scrd-sub-hdr", text="Bowler")
                if bowling_header and isinstance(bowling_header, Tag):
                    current_element = bowling_header.find_next_sibling("div")
                    while current_element and isinstance(current_element, Tag) and "cb-scrd-itms" in (current_element.get("class") or []):
                        cols = [col for col in current_element.find_all("div", class_="cb-col") if isinstance(col, Tag)]
                        if len(cols) >= 8 and cols[0].find("a"):
                            bowler_link = cols[0].find("a")
                            if bowler_link and isinstance(bowler_link, Tag):
                                bowler_name = bowler_link.get_text(strip=True)
                            else:
                                bowler_name = cols[0].get_text(strip=True)
                            inning_data["bowling"].append({
                                "player": bowler_name,
                                "O": cols[1].get_text(strip=True) if len(cols) > 1 else "",
                                "M": cols[2].get_text(strip=True) if len(cols) > 2 else "",
                                "R": cols[3].get_text(strip=True) if len(cols) > 3 else "",
                                "W": cols[4].get_text(strip=True) if len(cols) > 4 else "",
                                "NB": cols[5].get_text(strip=True) if len(cols) > 5 else "",
                                "WD": cols[6].get_text(strip=True) if len(cols) > 6 else "",
                                "ECO": cols[7].get_text(strip=True) if len(cols) > 7 else "",
                            })
                        current_element = current_element.find_next_sibling("div")

                scorecard["innings"].append(inning_data)
            return scorecard
        except Exception as e:
            logger.error(f"An error occurred during scorecard scraping: {e}", exc_info=True)
            return {"error": "Failed to parse the scorecard page."}

    def get_match_details(self, match_url: str) -> Optional[Dict[str, Any]]:
        """
        Scrapes detailed information about a specific match, given its URL.
        """
        logger.info(f"Scraping match details from {match_url}...")
        try:
            response = self.session.get(match_url)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")

            match_details: Dict[str, Any] = {}

            # Extracting match header
            header = soup.find("h1", class_="cb-nav-hdr")
            if header and isinstance(header, Tag):
                match_details["header"] = header.get_text(strip=True)

            # Extracting match status
            status = soup.find("div", class_="cb-text-live")
            if not status:
                status = soup.find("div", class_="cb-text-complete")
            if status and isinstance(status, Tag):
                match_details["status"] = status.get_text(strip=True)

            # Extracting scorecard
            scores: List[str] = []
            score_elements = soup.find_all("div", class_="cb-col-100 cb-col")
            for score_elem in score_elements:
                if isinstance(score_elem, Tag):
                    score_text = score_elem.get_text(strip=True)
                    if "Innings" in score_text:
                        scores.append(score_text)
            match_details["scores"] = scores

            # Extracting match info
            match_info: Dict[str, str] = {}
            info_wrapper_tag = soup.find(lambda tag: tag.name == "div" and "Match Info" in tag.get_text())
            if info_wrapper_tag and isinstance(info_wrapper_tag, Tag):
                info_wrapper = info_wrapper_tag.find_parent("div", class_="cb-col-100")
                if info_wrapper and isinstance(info_wrapper, Tag):
                    for item in info_wrapper.find_all("div", class_="cb-mtch-info-itm"):
                        if isinstance(item, Tag):
                            key_elem = item.find("div", class_="cb-col-27")
                            val_elem = item.find("div", class_="cb-col-73")
                            if key_elem and val_elem and isinstance(key_elem, Tag) and isinstance(val_elem, Tag):
                                key = key_elem.get_text(strip=True)
                                value = val_elem.get_text(strip=True)
                                match_info[key] = value
            match_details["match_info"] = match_info

            return match_details
        except Exception as e:
            logger.error(f"An error occurred while scraping match details: {e}", exc_info=True)
            return None
    def _extract_players(self, col_div: Optional[Tag]) -> List[Dict[str, str]]:
        """
        A robust helper method to extract player details from a squad column.
        """
        players: List[Dict[str, str]] = []
        if not col_div:
            return players

        # Find all player 'a' tags using a more specific CSS selector
        player_cards = col_div.select('a[class*="cb-player-card-"]')
        
        for card in player_cards:
            name_container = card.select_one('div[class*="cb-player-name-"]')
            if not name_container:
                continue

            # The actual name/role text is in a nested div
            inner_div = name_container.find('div')
            if not inner_div:
                continue

            # **FIX**: Correctly extract the name, which is the first text node.
            name = inner_div.contents[0].strip() if inner_div.contents else "N/A"
            
            # Extract role from the specific span tag
            role_span = inner_div.find('span', class_='text-gray')
            role = role_span.get_text(strip=True) if role_span else ""
            
            profile_url = "https://www.cricbuzz.com" + (card.get("href") or "")
            
            players.append({'name': name, 'role': role, 'profile_url': profile_url})
        return players

    def get_squads(self, squads_url: str) -> Dict[str, Any]:
        """
        Scrapes the squads (playing XI, bench, support staff) from a given Cricbuzz match squads URL.
        This method has been refactored for improved parsing accuracy and robustness.
        """
        logger.info(f"Scraping squads from {squads_url}...")
        try:
            match_id = squads_url.split("/")[-2]
            api_url = f"https://www.cricbuzz.com/api/html/match-squads/{match_id}"
            response = self.session.get(api_url)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")

            squads: Dict[str, Any] = {}

            # Extract team names
            team1_link = soup.select_one("a.cb-team1")
            team2_link = soup.select_one("a.cb-team2")
            team1 = team1_link.get_text(strip=True) if team1_link else "Team1"
            team2 = team2_link.get_text(strip=True) if team2_link else "Team2"

            squads[team1] = {"playing_xi": [], "bench": [], "support_staff": []}
            squads[team2] = {"playing_xi": [], "bench": [], "support_staff": []}

            # **FIX**: Refactored logic to be less repetitive and more robust
            section_mapping = {
                "Playing XI": "playing_xi",
                "Bench": "bench",
                "Support staff": "support_staff",
            }

            for title, key in section_mapping.items():
                header = soup.find("div", class_="cb-pl11-hdr", text=re.compile(title))
                if header and isinstance(header, Tag):
                    # The two team columns are the immediate next two sibling divs
                    columns = header.find_next_siblings("div", limit=2)
                    if len(columns) == 2:
                        left_col, right_col = columns
                        squads[team1][key] = self._extract_players(left_col)
                        squads[team2][key] = self._extract_players(right_col)
            
            return squads
        except Exception as e:
            logger.error(f"An error occurred during squads scraping: {e}", exc_info=True)
            return {"error": "Failed to parse the squads page."}



def test_full_scraper_flow():
    """Test function to demonstrate the full workflow of the CricbuzzConnector."""
    print("\n" + "="*50)
    print("🏏 Starting Full Cricbuzz Scraper Test 🏏")
    print("="*50)

    try:
        print("\n--> Step 1: Initializing the connector...")
        connector = CricbuzzConnector(
            linked_account=None, # type: ignore
            security_scheme=NoAuthScheme(),
            security_credentials=NoAuthSchemeCredentials(),
        )
        print("    ✅ Connector initialized successfully.")
    except Exception as e:
        print(f"    ❌ FATAL: Connector initialization failed: {e}")
        return

    print("\n--> Step 2: Getting live matches to find a scorecard URL...")
    matches = connector.get_live_matches()
    if not matches or (isinstance(matches, list) and len(matches) > 0 and "error" in matches[0]):
        print(f"    ❌ Could not retrieve matches. Error: {matches[0].get('error') if matches else 'Unknown'}")
        return

    print(f"    ✅ Success! Found {len(matches)} matches.")
    
    print("\n    All matches found:")
    for i, match in enumerate(matches, 1):
        print(f"      {i}. {match}")
    
    # Find a match with scorecard link
    scorecard_url = None
    for match in matches:
        if match.get("scorecard_url"):
            scorecard_url = match["scorecard_url"]
            print(f"\n    Using match: {match['title']}")
            print(f"    Scorecard URL: {scorecard_url}")
            break
    
    if not scorecard_url:
        print("    ❌ No scorecard URLs found in any matches.")
        print("    Available matches:")
        for i, match in enumerate(matches):
            print(f"      {i+1}. {match.get('title', 'No title')}")
            if match.get('scorecard_url'):
                print(f"         Scorecard URL: {match['scorecard_url']}")
        return
    
    print(f"\n--> Step 3: Scraping full scorecard...")
    scorecard = connector.get_full_scorecard(scorecard_url)

    if not scorecard or "error" in scorecard:
        print(f"    ❌ An error occurred: {scorecard.get('error', 'Unknown error')}")
        return
        
    print("    ✅ Success! Scorecard parsed.")
    print("\n--- Match Info ---")
    for key, value in scorecard.get("match_info", {}).items():
        print(f"    - {key}: {value}")

    for inning in scorecard.get("innings", []):
        print(f"\n--- {inning.get('title')} ---")
        print("\n    Batting:")
        for batter in inning.get("batting", []):
            print(f"    - {batter['player']:<25} {batter['dismissal']:<40} R:{batter['R']:>3} B:{batter['B']:>3} SR:{batter['SR']}")
        print("\n    Bowling:")
        for bowler in inning.get("bowling", []):
            print(f"    - {bowler['player']:<25} O:{bowler['O']:>4} M:{bowler['M']:>2} R:{bowler['R']:>3} W:{bowler['W']:>2} ECO:{bowler['ECO']}")

    print(f"\n--> Step 4: Scraping squads...")
    squads_url = scorecard_url.replace("live-cricket-scorecard", "cricket-match-squads")
    squads = connector.get_squads(squads_url)

    if not squads or "error" in squads:
        print(f"    ❌ An error occurred: {squads.get('error', 'Unknown error')}")
        return
        
    print("    ✅ Success! Squads parsed.")
    for team, data in squads.items():
        print(f"\n--- {team} ---")
        for category, players in data.items():
            print(f"\n    {category.replace('_', ' ').title()}:")
            for player in players:
                print(f"    - {player['name']} ({player['role']})")

    print("\n" + "="*50)
    print("🎉 All scraper tests completed. 🎉")
    print("="*50)

if __name__ == "__main__":
    test_full_scraper_flow()