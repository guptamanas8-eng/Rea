"""Built-in content for the TV games. Add your own from the remote's Games tab, or edit these lists."""

# Draw & Guess: easy to draw, easy to guess, family-friendly.
WORDS = """
cat dog house tree sun moon star car bus train airplane boat fish bird apple banana mango pizza cake
umbrella chair table bed clock phone television book pencil ball kite flower rainbow cloud snowman
mountain river bridge castle rocket robot guitar drum crown glasses shoe hat shirt key door window
ladder candle balloon gift heart butterfly spider snake elephant giraffe lion monkey rabbit turtle
horse cow duck owl penguin whale shark octopus crab bicycle scooter tractor helicopter lighthouse tent
campfire volcano island beach football swing toothbrush spoon cup teapot bottle lamp fan laptop camera
headphones bell anchor flag map dice cupcake sandwich carrot corn watermelon cherry grapes pineapple
egg cheese cookie donut popcorn igloo pyramid windmill frog bee ant snail dinosaur dragon ghost
pumpkin mushroom cactus palm tree ice cream cricket bat rain lightning tornado
""".split()
# two-word entries
WORDS = sorted(set(WORDS) - {"palm", "ice", "cream", "cricket", "bat"}) + ["palm tree", "ice cream", "cricket bat"]

# Quiz buzzer: (question, correct answer, wrong answers...)
QUESTIONS = [
    ("How many continents are there?", "7", "5", "6", "8"),
    ("Which planet is known as the Red Planet?", "Mars", "Venus", "Jupiter", "Mercury"),
    ("What is the largest ocean on Earth?", "Pacific Ocean", "Atlantic Ocean", "Indian Ocean", "Arctic Ocean"),
    ("How many legs does a spider have?", "8", "6", "10", "12"),
    ("Which gas do plants take in from the air to make food?", "Carbon dioxide", "Oxygen", "Nitrogen", "Helium"),
    ("What is the capital of Japan?", "Tokyo", "Kyoto", "Osaka", "Seoul"),
    ("How many days are there in a leap year?", "366", "365", "364", "360"),
    ("What is the national animal of India?", "Bengal tiger", "Lion", "Elephant", "Peacock"),
    ("What is the national bird of India?", "Peacock", "Parrot", "Sparrow", "Eagle"),
    ("Who wrote India's national anthem, Jana Gana Mana?", "Rabindranath Tagore", "Bankim Chandra Chatterjee",
     "Sarojini Naidu", "Mahatma Gandhi"),
    ("At sea level, water boils at…", "100 °C", "90 °C", "80 °C", "120 °C"),
    ("Which is the largest planet in our solar system?", "Jupiter", "Saturn", "Earth", "Neptune"),
    ("How many players does one cricket team have on the field?", "11", "9", "10", "12"),
    ("What colour do you get by mixing blue and yellow?", "Green", "Purple", "Orange", "Brown"),
    ("Which is the highest mountain above sea level?", "Mount Everest", "K2", "Kangchenjunga", "Mont Blanc"),
    ("In which city is the Taj Mahal?", "Agra", "Delhi", "Jaipur", "Lucknow"),
    ("How many sides does a hexagon have?", "6", "5", "7", "8"),
    ("What is the hardest natural substance?", "Diamond", "Gold", "Iron", "Quartz"),
    ("Which organ pumps blood around the body?", "Heart", "Lungs", "Liver", "Brain"),
    ("At what temperature does water freeze?", "0 °C", "10 °C", "-10 °C", "32 °C"),
    ("What is the smallest prime number?", "2", "1", "3", "0"),
    ("Which planet is closest to the Sun?", "Mercury", "Venus", "Earth", "Mars"),
    ("What do bees make?", "Honey", "Milk", "Silk", "Cotton"),
    ("Which festival is called the festival of lights?", "Diwali", "Holi", "Eid", "Onam"),
    ("What is the largest animal on Earth?", "Blue whale", "Elephant", "Giraffe", "Hippopotamus"),
    ("What is 7 × 8?", "56", "54", "64", "48"),
    ("Who was the first person to walk on the Moon?", "Neil Armstrong", "Buzz Aldrin", "Yuri Gagarin",
     "Rakesh Sharma"),
    ("Kangaroos are native to which country?", "Australia", "South Africa", "Brazil", "India"),
    ("How many colours are traditionally named in a rainbow?", "7", "5", "6", "8"),
    ("What is the currency of Japan?", "Yen", "Won", "Yuan", "Ringgit"),
    ("Which instrument usually has 88 keys?", "Piano", "Guitar", "Violin", "Flute"),
    ("What is the capital of Australia?", "Canberra", "Sydney", "Melbourne", "Perth"),
    ("Which sport uses a shuttlecock?", "Badminton", "Tennis", "Squash", "Table tennis"),
    ("Sunlight helps your body make which vitamin?", "Vitamin D", "Vitamin C", "Vitamin A", "Vitamin B12"),
    ("How many teeth does an adult usually have (including wisdom teeth)?", "32", "28", "30", "36"),
    ("What is the fastest land animal?", "Cheetah", "Lion", "Horse", "Leopard"),
    ("In which year did India become independent?", "1947", "1950", "1942", "1952"),
    ("What was the name of India's first satellite?", "Aryabhata", "Bhaskara", "Rohini", "Chandrayaan"),
    ("What is H₂O better known as?", "Water", "Salt", "Sugar", "Air"),
    ("Which ocean lies to the south of India?", "Indian Ocean", "Pacific Ocean", "Atlantic Ocean",
     "Arctic Ocean"),
    ("Who painted the Mona Lisa?", "Leonardo da Vinci", "Pablo Picasso", "Vincent van Gogh", "Michelangelo"),
    ("Which is the largest country by area?", "Russia", "Canada", "China", "United States"),
    ("A caterpillar grows up to become a…", "Butterfly or moth", "Bee", "Beetle", "Spider"),
    ("How many strings does a standard guitar have?", "6", "4", "5", "7"),
    ("How many minutes are there in an hour?", "60", "100", "30", "90"),
    ("Which is the longest bone in the human body?", "Femur (thigh bone)", "Spine", "Skull", "Rib"),
    ("Which planet has the most famous rings?", "Saturn", "Mars", "Venus", "Mercury"),
    ("What does a thermometer measure?", "Temperature", "Weight", "Speed", "Time"),
    ("How many wheels does a tricycle have?", "3", "2", "4", "1"),
    ("Which is the closest star to Earth?", "The Sun", "Polaris", "Sirius", "Alpha Centauri"),
]
