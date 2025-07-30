a=int(input())
b=str(a).replace('-','')
c=b[::-1]
if int(c)==int(b):
    print('true')
else:
    print('false')