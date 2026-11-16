import bs4
import requests

def extract_href(a):
    return a['href']

def get_team_soup(team_path):
    response = requests.get("https://www.ahahockey.com" + team_path)
    return bs4.BeautifulSoup(response.text, 'html.parser')

def get_team_name(soup):
    return soup.select_one('.team-info .info h1 div').previous_sibling.strip()

def get_team_level(soup):
    return soup.select_one('.team-info .season h3').get_text().split(' ')[0]

def get_players_for_team(soup):
    return list(map(extract_href, soup.select('.sortable-table a[href^="/player"]')))

def get_teams():
    response = requests.get("https://www.ahahockey.com/standings/twin-cities")
    soup = bs4.BeautifulSoup(response.text, 'html.parser')
    return list(map(extract_href, soup.select('.team-col a')))

def get_player_soup(player_path):
    response = requests.get("https://www.ahahockey.com" + player_path)
    return bs4.BeautifulSoup(response.text, 'html.parser')

def get_player_id(player_path):
    return player_path.split('/')[2]

def multiple_teams_at_level(soup, level_to_check):
    at_level = 0
    for team in soup.select('.player-stats .current .team-info .content h4'):
        level = team.get_text().split(' ')[0]
        if level == level_to_check:
            at_level += 1
    return at_level > 1

players = {}
for team in get_teams(): 
    team_soup = get_team_soup(team)
    level = get_team_level(team_soup)
    players_on_team = get_players_for_team(team_soup)
    player_conflicts = 0
    for player_path in players_on_team:
        player_id = get_player_id(player_path)
        if player_id not in players:
            players[player_id] = False
        player_soup = get_player_soup(player_path)
        if multiple_teams_at_level(player_soup, level):
            players[player_id] = True
            player_conflicts += 1
    print(level, "-", get_team_name(team_soup), "- rostered players:", len(players_on_team), "conflicting players:", player_conflicts, "percentage:", float(player_conflicts)/len(players_on_team)*100)

active_players = len(players)
conflicting_players = list(players.values()).count(True)
print("Active Players:",  active_players)
print("Conflicting Players:", conflicting_players)
print("Percentage Affected:", float(conflicting_players)/active_players*100)

