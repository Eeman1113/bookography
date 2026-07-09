"""Download specific Project Gutenberg books by title/author into ./books.

Uses the Gutendex API (https://gutendex.com) to resolve title+author to a
Gutenberg ID, then downloads the plain-text version of each book.
"""

import os
import re
import sys
import time
import requests

BOOKS = [
    ("Moby Dick; Or, The Whale", "Herman Melville"),
    ("Pride and Prejudice", "Jane Austen"),
    ("A Room with a View", "E. M. Forster"),
    ("Romeo and Juliet", "William Shakespeare"),
    ("Crime and Punishment", "Fyodor Dostoyevsky"),
    ("Alice's Adventures in Wonderland", "Lewis Carroll"),
    ("The Blue Castle: a novel", "L. M. Montgomery"),
    ("The Complete Works of William Shakespeare", "William Shakespeare"),
    ("The Adventures of Sherlock Holmes", "Arthur Conan Doyle"),
    ("The Count of Monte Cristo", "Alexandre Dumas"),
    ("Middlemarch", "George Eliot"),
    ("The City of God, Volume I", "Augustine"),
    ("The Secret of Chimneys", "Agatha Christie"),
    ("My Life — Volume 1", "Richard Wagner"),
    ("Jane Eyre: An Autobiography", "Charlotte Brontë"),
    ("The Mysteries of Udolpho", "Ann Ward Radcliffe"),
    ("Frankenstein; or, the modern prometheus", "Mary Wollstonecraft Shelley"),
    ("Carmen", "Prosper Mérimée"),
    ("Little Women; Or, Meg, Jo, Beth, and Amy", "Louisa May Alcott"),
    ("Dracula", "Bram Stoker"),
    ("Bidwell's Travels, from Wall Street to London Prison: Fifteen Years in Solitude", "Austin Bidwell"),
    ("The Green Mummy", "Fergus Hume"),
    ("The Love Letters of Mary Wollstonecraft to Gilbert Imlay", "Mary Wollstonecraft"),
    ("The strange case of Dr. Jekyll and Mr. Hyde", "Robert Louis Stevenson"),
    ("Meditations", "Marcus Aurelius"),
    ("Twenty years after", "Alexandre Dumas"),
    ("The King in Yellow", "Robert W. Chambers"),
    ("The String of Pearls; Or, The Barber of Fleet Street. A Domestic Romance.", "Prest Rymer"),
    ("Le Fantôme de l'Opéra", "Gaston Leroux"),
    ("The Lady of the Lake", "Walter Scott"),
    ("The Man Who Was Thursday: A Nightmare", "G. K. Chesterton"),
    ("The Extraordinary Adventures of Arsène Lupin, Gentleman-Burglar", "Maurice Leblanc"),
    ("Sense and Sensibility", "Jane Austen"),
    ("The Book of the Thousand Nights and a Night — Volume 01", ""),
    ("The Monk: A Romance", "M. G. Lewis"),
    ("Four Arthurian Romances", "Chrétien de Troyes"),
    ("Thuvia, maid of Mars", "Edgar Rice Burroughs"),
    ("A farewell to arms", "Ernest Hemingway"),
    ("The Confessions of St. Augustine", "Augustine"),
    ("Eloisa", "Jean-Jacques Rousseau"),
    ("The Yeoman Adventurer", "George W. Gough"),
    ("Manon Lescaut", "Prévost"),
    ("A Honeymoon in Space", "George Chetwynd Griffith"),
    ("The Misplaced Battleship", "Harry Harrison"),
    ("Modern English biography, volume 2", "Frederic Boase"),
    ("Life on the Mississippi", "Mark Twain"),
    ("The Brothers Karamazov", "Fyodor Dostoyevsky"),
    ("The History of Sir Richard Calmady: A Romance", "Lucas Malet"),
    ("The Mystery of Edwin Drood", "Charles Dickens"),
    ("The Complete Project Gutenberg Works of Jane Austen", "Jane Austen"),
    ("The 2006 CIA World Factbook", "United States. Central Intelligence Agency"),
    ("The Adventures of Tom Sawyer, Complete", "Mark Twain"),
    ("The Enchanted April", "Elizabeth Von Arnim"),
    ("I am a woman", "Ann Bannon"),
    ("Oliver Twist, Vol. 2", "Charles Dickens"),
    ("A Study in Scarlet", "Arthur Conan Doyle"),
    ("Autobiography of Benjamin Franklin", "Benjamin Franklin"),
    ("Robert Orange", "John Oliver Hobbes"),
    ("Dr. Mabuse, der Spieler", "Norbert Jacques"),
    ("King Arthur and the Knights of the Round Table", "Thomas Malory"),
    ("That Girl Montana", "Marah Ellis Ryan"),
    ("The Sex Life of the Gods", "M. E. Knerr"),
    ("The Hound of the Baskervilles", "Arthur Conan Doyle"),
    ("The Countess of Pembroke's Arcadia", "Philip Sidney"),
    ("Cranford", "Elizabeth Cleghorn Gaskell"),
    ("Under the Red Dragon: A Novel", "James Grant"),
    ("The Blue Lagoon: A Romance", "H. De Vere Stacpoole"),
    ("Narrative of the Life of Frederick Douglass, an American Slave", "Frederick Douglass"),
    ("The Adventures of Ferdinand Count Fathom — Complete", "T. Smollett"),
    ("The Expedition of Humphry Clinker", "T. Smollett"),
    ("Catriona", "Robert Louis Stevenson"),
    ("Henrietta Temple: A Love Story", "Benjamin Disraeli"),
    ("Blow The Man Down: A Romance Of The Coast", "Holman Day"),
    ("Roméo et Juliette", "William Shakespeare"),
    ("The Works of Edgar Allan Poe — Volume 2", "Edgar Allan Poe"),
    ("The Adventures of Roderick Random", "T. Smollett"),
    ("History of Tom Jones, a Foundling", "Henry Fielding"),
    ("The Great Gatsby", "F. Scott Fitzgerald"),
    ("The murder of Roger Ackroyd", "Agatha Christie"),
    ("Spoon River Anthology", "Edgar Lee Masters"),
    ("Around the World in Eighty Days", "Jules Verne"),
    ("The Turn of the Screw", "Henry James"),
    ("Emily Brontë", "A. Mary F. Robinson"),
    ("The Lives of the Twelve Caesars, Complete", "Suetonius"),
    ("The Sorrows of Young Werther", "Johann Wolfgang von Goethe"),
    ("Erotica Romana", "Johann Wolfgang von Goethe"),
    ("Ecce Homo", "Friedrich Wilhelm Nietzsche"),
    ("Love and Freindship", "Jane Austen"),
    ("The Odyssey", "Homer"),
    ("Sonnets from the Portuguese", "Elizabeth Barrett Browning"),
    ("Aus dem Leben eines Taugenichts: Novelle", "Joseph Eichendorff"),
    ("Only a girl's love", "Charles Garvice"),
    ("A Journey to the Centre of the Earth", "Jules Verne"),
    ("Treasure Island", "Robert Louis Stevenson"),
    ("The Home Book of Verse — Volume 2", "Burton Egbert Stevenson"),
    # From the original list — books not in the new list.
    ("The Red Lily — Complete", "Anatole France"),
    ("The Landloper: The Romance of a Man on Foot", "Holman Day"),
    ("The Romance of Tristan and Iseult", "Joseph Bédier"),
    ("Victorian Short Stories: Stories of Courtship", ""),
    ("Linnet: A Romance", "Grant Allen"),
    ("Complete Short Works of George Meredith", "George Meredith"),
    ("India's Love Lyrics", "Laurence Hope"),
    ("The Green Mouse", "Robert W. Chambers"),
    ("Lorna Doone: A Romance of Exmoor", "R. D. Blackmore"),
    ("Undine", "La Motte-Fouqué"),
    ("Dainty's Cruel Rivals; Or, The Fatal Birthday", "Mrs. Alex. McVeigh Miller"),
    ("Camille (La Dame aux Camilias)", "Alexandre Dumas"),
    ("As You Like It", "William Shakespeare"),
    ("A Romance of Billy-Goat Hill", "Alice Caldwell Hegan Rice"),
    ("Moods", "Louisa May Alcott"),
    ("That Which Hath Wings: A Novel of the Day", ""),
    ("The Hero of the People: A Historical Romance of Love, Liberty and Loyalty", "Alexandre Dumas"),
    ("The Boy with Wings", "Berta Ruck"),
    ("Doctor Cupid: A Novel", "Rhoda Broughton"),
    ("A pair of blue eyes", "Thomas Hardy"),
    ("The Diary of Samuel Pepys — Complete", "Samuel Pepys"),
    ("Cynthia's Chauffeur", "Louis Tracy"),
    ("On the Cross: A Romance of the Passion Play at Oberammergau", "Wilhelmine von Hillern"),
    ("The Pirate", "Walter Scott"),
    ("Forest Days: A Romance of Old Times", "G. P. R. James"),
    ("Autobiography of a Yogi", "Paramahansa Yogananda"),
    ("What Will People Say? A Novel", "Rupert Hughes"),
]

BOOKS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "books")
os.makedirs(BOOKS_DIR, exist_ok=True)


def slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"\s+", "_", text.strip())
    return text[:120]


def find_book_id(title: str, author: str) -> int | None:
    """Query Gutendex for the best match. Returns Gutenberg ID or None."""
    # Search using the leading title words + author last name for robustness.
    title_core = re.split(r"[:;—\-]", title)[0].strip()
    query = f"{title_core} {author}".strip()
    try:
        r = requests.get("https://gutendex.com/books", params={"search": query}, timeout=30)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"  ! search failed: {e}")
        return None

    results = r.json().get("results", [])
    if not results:
        return None

    title_lower = title_core.lower()
    author_lower = author.lower()

    # Prefer results whose title starts with the core title and whose author matches.
    def score(book):
        t = book.get("title", "").lower()
        authors = " ".join(a.get("name", "").lower() for a in book.get("authors", []))
        s = 0
        if t.startswith(title_lower):
            s += 3
        elif title_lower in t:
            s += 2
        if author_lower and any(part in authors for part in author_lower.split() if len(part) > 2):
            s += 2
        # Prefer English or original language matches with high download counts.
        s += min(book.get("download_count", 0) / 10000, 2)
        return s

    results.sort(key=score, reverse=True)
    return results[0].get("id")


def download_book(book_id: int, filename: str) -> bool:
    """Download the plain-text version of a Gutenberg book."""
    urls = [
        f"https://www.gutenberg.org/cache/epub/{book_id}/pg{book_id}.txt",
        f"https://www.gutenberg.org/files/{book_id}/{book_id}-0.txt",
        f"https://www.gutenberg.org/files/{book_id}/{book_id}.txt",
    ]
    for url in urls:
        try:
            r = requests.get(url, timeout=60)
            if r.status_code == 200 and len(r.content) > 500:
                with open(filename, "wb") as f:
                    f.write(r.content)
                return True
        except requests.RequestException:
            continue
    return False


def main():
    total = len(BOOKS)
    ok = 0
    failed = []
    for i, (title, author) in enumerate(BOOKS, 1):
        base = slugify(f"{title}_{author}" if author else title)
        filename = os.path.join(BOOKS_DIR, f"{base}.txt")

        print(f"[{i}/{total}] {title} — {author or '(anon)'}")
        if os.path.exists(filename):
            print(f"  · already have {os.path.basename(filename)}, skipping")
            ok += 1
            continue
        book_id = find_book_id(title, author)
        if book_id is None:
            print("  ! no match on Gutendex")
            failed.append((title, author, "no match"))
            continue

        if download_book(book_id, filename):
            print(f"  ✓ id={book_id} -> {os.path.basename(filename)}")
            ok += 1
        else:
            print(f"  ! id={book_id} download failed")
            failed.append((title, author, f"id={book_id} download failed"))

        # Be polite to both APIs.
        time.sleep(0.4)

    print(f"\nDone: {ok}/{total} downloaded.")
    if failed:
        print("Failures:")
        for t, a, reason in failed:
            print(f"  - {t} / {a} :: {reason}")


if __name__ == "__main__":
    sys.exit(main())
