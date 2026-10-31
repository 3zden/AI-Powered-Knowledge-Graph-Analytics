from resolve import clean, resolve_names

assert clean("Dr. Amara Okafor") == "Amara Okafor"
assert clean("Lumen Batteries S.p.A.") == "Lumen Batteries"
m = resolve_names([
    ("Helios Robotics SA", "Organization"), ("Helios Robotics", "Organization"), ("Helios", "Organization"),
    ("Dr. Amara Okafor", "Person"), ("A. Okafor", "Person"), ("Amara Okafor", "Person"),
    ("Northwind Capital Partners LLP", "Organization"), ("Northwind Capital", "Organization"),
    ("Northwind Capital", "Organization"), ("Lumen Batteries", "Organization"),
    ("Lyon", "Location"), ("London", "Location"),
])
assert {m["Helios"], m["Helios Robotics SA"]} == {"Helios Robotics"}, m
assert {m["A. Okafor"], m["Dr. Amara Okafor"]} == {"Amara Okafor"}, m
assert m["Northwind Capital Partners LLP"] == "Northwind Capital", m
assert m["Lyon"] != m["London"] and m["Lumen Batteries"] != m["Helios"], m
print("test_resolve ok")
